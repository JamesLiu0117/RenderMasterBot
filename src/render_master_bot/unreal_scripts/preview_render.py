"""Build a temporary scene and render one PNG through Movie Render Pipeline."""

from __future__ import annotations

import glob
import json
import os
import sys
import traceback

import unreal


SCRIPT_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

from scene_build import spawn_camera, spawn_light, spawn_object, write_result  # noqa: E402


EXECUTOR = None
QUEUE = None
SEQUENCE = None
RESULT = None
OUTPUT_PATH = None
PREVIEW_DIRECTORY = None
FINISHED = False


def create_still_sequence(scene_name, camera_actor):
    asset_name = f"RMB_{scene_name}_Preview"
    sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        asset_name,
        "/Game/RenderMasterBot/Transient",
        unreal.LevelSequence,
        unreal.LevelSequenceFactoryNew(),
    )
    if sequence is None:
        raise RuntimeError("Unreal could not create the in-memory preview LevelSequence")
    sequence.set_playback_start(0)
    sequence.set_playback_end(1)

    binding = sequence.add_possessable(camera_actor)
    camera_cut_track = sequence.add_track(unreal.MovieSceneCameraCutTrack)
    camera_cut_section = camera_cut_track.add_section()
    camera_cut_section.set_start_frame(-1)
    camera_cut_section.set_end_frame(1)
    camera_binding_id = unreal.MovieSceneObjectBindingID()
    camera_binding_id.set_editor_property("Guid", binding.get_id())
    camera_cut_section.set_editor_property("CameraBindingID", camera_binding_id)
    return sequence


def configure_queue(sequence, request, output_directory):
    queue = unreal.MoviePipelineQueue()
    job = unreal.MoviePipelineEditorLibrary.create_job_from_sequence(queue, sequence)
    if job is None:
        raise RuntimeError("Unreal could not create a Movie Render Pipeline job")
    job.set_editor_property("job_name", request["scene_name"])

    configuration = job.get_configuration()
    output = configuration.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    output.set_editor_property(
        "output_directory",
        unreal.DirectoryPath(path=output_directory),
    )
    output.set_editor_property(
        "output_resolution",
        unreal.IntPoint(
            x=int(request["render"]["width_px"]),
            y=int(request["render"]["height_px"]),
        ),
    )
    output.set_editor_property("file_name_format", "beauty")
    output.set_editor_property("zero_pad_frame_numbers", 4)
    output.set_editor_property("flush_disk_writes_per_shot", True)
    output.set_editor_property("override_existing_output", False)

    configuration.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    configuration.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)
    return queue


def preview_files():
    pattern = os.path.join(PREVIEW_DIRECTORY, "**", "*.png")
    return sorted(os.path.abspath(path) for path in glob.glob(pattern, recursive=True))


def finish_render(executor, success):
    del executor
    global FINISHED
    if FINISHED:
        return
    FINISHED = True
    try:
        files = preview_files()
        RESULT["preview_files"] = files
        if success and files:
            RESULT["status"] = "succeeded"
            unreal.log(
                f"RENDERMASTER_PREVIEW_OK output={OUTPUT_PATH} "
                f"files={len(files)}"
            )
        else:
            RESULT["status"] = "failed"
            if not success:
                RESULT["errors"].append("Movie Render Pipeline reported failure")
            if not files:
                RESULT["errors"].append("Movie Render Pipeline produced no PNG preview")
            unreal.log_error("RENDERMASTER_PREVIEW_FAILED: " + "; ".join(RESULT["errors"]))
        write_result(OUTPUT_PATH, RESULT)
    except Exception as exc:
        RESULT["status"] = "failed"
        RESULT["errors"].append(str(exc)[:4000])
        write_result(OUTPUT_PATH, RESULT)
        unreal.log_error(f"RENDERMASTER_PREVIEW_CALLBACK_FAILED: {exc}")
        unreal.log_error(traceback.format_exc())
    finally:
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)


def main():
    request_path = os.environ.get("RENDERMASTER_PREVIEW_REQUEST")
    output_path = os.environ.get("RENDERMASTER_PREVIEW_RESULT")
    preview_directory = os.environ.get("RENDERMASTER_PREVIEW_DIRECTORY")
    if not request_path:
        raise RuntimeError("RENDERMASTER_PREVIEW_REQUEST is required")
    if not output_path:
        raise RuntimeError("RENDERMASTER_PREVIEW_RESULT is required")
    if not preview_directory:
        raise RuntimeError("RENDERMASTER_PREVIEW_DIRECTORY is required")

    with open(request_path, "r", encoding="utf-8-sig") as stream:
        request = json.load(stream)
    os.makedirs(preview_directory, exist_ok=True)

    project_name = os.path.splitext(
        os.path.basename(unreal.Paths.get_project_file_path())
    )[0]
    global RESULT, OUTPUT_PATH, PREVIEW_DIRECTORY
    OUTPUT_PATH = output_path
    PREVIEW_DIRECTORY = preview_directory
    RESULT = {
        "schema_version": "0.1",
        "status": "failed",
        "project_name": project_name,
        "world_name": None,
        "scene_name": request["scene_name"],
        "render_spec_sha256": request["render_spec_sha256"],
        "actors": [],
        "preview_files": [],
        "warnings": [],
        "errors": [],
    }

    try:
        actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = editor_subsystem.get_editor_world()
        if actor_subsystem is None or world is None:
            raise RuntimeError("the Unreal Editor world or actor subsystem is unavailable")
        RESULT["world_name"] = str(world.get_name())

        for scene_object in request["objects"]:
            RESULT["actors"].append(
                spawn_object(actor_subsystem, scene_object, transient=False)
            )
        camera_actor_object, camera_record = spawn_camera(
            actor_subsystem,
            request["camera"],
            transient=False,
            return_actor=True,
        )
        RESULT["actors"].append(camera_record)

        for light in request["lights"]:
            RESULT["actors"].append(
                spawn_light(actor_subsystem, light, transient=False)
            )

        global SEQUENCE, QUEUE, EXECUTOR
        SEQUENCE = create_still_sequence(request["scene_name"], camera_actor_object)
        QUEUE = configure_queue(SEQUENCE, request, preview_directory)
        subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
        EXECUTOR = unreal.MoviePipelinePIEExecutor(subsystem)
        EXECUTOR.set_is_rendering_offscreen(True)
        EXECUTOR.set_allow_using_unsaved_levels(True)
        EXECUTOR.on_executor_finished_delegate.add_callable_unique(finish_render)
        unreal.EditorPythonScripting.set_keep_python_script_alive(True)
        subsystem.render_queue_instance_with_executor_instance(QUEUE, EXECUTOR)
        unreal.log(
            f"RENDERMASTER_PREVIEW_STARTED scene={request['scene_name']} "
            f"world={RESULT['world_name']}"
        )
    except Exception as exc:
        RESULT["errors"].append(str(exc)[:4000])
        write_result(OUTPUT_PATH, RESULT)
        unreal.log_error(f"RENDERMASTER_PREVIEW_SETUP_FAILED: {exc}")
        unreal.log_error(traceback.format_exc())
        unreal.EditorPythonScripting.set_keep_python_script_alive(False)


if __name__ == "__main__":
    main()
