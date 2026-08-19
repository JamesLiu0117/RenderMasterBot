#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "Editor.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/PointLightComponent.h"
#include "Engine/PointLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RenderMasterManifest.h"
#include "RenderMasterCameraAssistant.h"
#include "RenderMasterCameraBatchAssistant.h"
#include "RenderMasterMaterialAssistant.h"
#include "RenderMasterPerformanceAssistant.h"
#include "RenderMasterRuntimePerformanceAssistant.h"
#include "RenderMasterInsightsGpuAssistant.h"
#include "RenderMasterLightAssistant.h"
#include "RenderMasterLightingRigAssistant.h"
#include "RenderMasterLightingRigReviewAssistant.h"
#include "RenderMasterTransformAssistant.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterManifestParseTest,
    "RenderMasterBot.Editor.Manifest.ParseLifecycle",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterManifestParseTest::RunTest(const FString& Parameters)
{
    const FString RunningJson = TEXT(R"JSON({
        "workflow_id": "panel_001",
        "status": "running",
        "stage": "rendering",
        "max_iterations": 3,
        "iterations": [],
        "errors": []
    })JSON");

    FRenderMasterManifestSnapshot Snapshot;
    FString Error;
    TestTrue(TEXT("Running manifest parses"), FRenderMasterManifestSnapshot::Parse(RunningJson, Snapshot, Error));
    TestEqual(TEXT("Workflow ID"), Snapshot.WorkflowId, FString(TEXT("panel_001")));
    TestEqual(TEXT("Running stage"), Snapshot.Stage, FString(TEXT("rendering")));
    TestEqual(TEXT("Maximum iterations"), Snapshot.MaxIterations, 3);
    TestFalse(TEXT("Running manifest is not terminal"), Snapshot.IsTerminal());

    const FString FinishedJson = TEXT(R"JSON({
        "workflow_id": "panel_001",
        "status": "succeeded",
        "stage": "complete",
        "stop_reason": "evaluator_passed",
        "max_iterations": 3,
        "iterations": [{"iteration": 1}],
        "errors": []
    })JSON");

    TestTrue(TEXT("Finished manifest parses"), FRenderMasterManifestSnapshot::Parse(FinishedJson, Snapshot, Error));
    TestTrue(TEXT("Finished manifest is terminal"), Snapshot.IsTerminal());
    TestEqual(TEXT("Iteration count"), Snapshot.IterationCount, 1);
    TestEqual(TEXT("Stop reason"), Snapshot.StopReason, FString(TEXT("evaluator_passed")));

    const FString InvalidJson = TEXT(R"JSON({"status":"running"})JSON");
    TestFalse(TEXT("Required fields are enforced"), FRenderMasterManifestSnapshot::Parse(InvalidJson, Snapshot, Error));
    TestTrue(TEXT("Invalid manifest explains the failure"), !Error.IsEmpty());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterMaterialProposalParseTest,
    "RenderMasterBot.Editor.Assistant.MaterialProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterMaterialProposalParseTest::RunTest(const FString& Parameters)
{
    const FString ProposalJson = TEXT(R"JSON({
        "proposal_id": "proposal_001",
        "status": "proposed",
        "request": "Make this look like old dark wood.",
        "target": {
            "schema_version": "0.1",
            "project_name": "OptimizationPlugin",
            "level_path": "/Game/Maps/TestMap",
            "actor_name": "Door",
            "actor_path": "/Game/Maps/TestMap.TestMap:PersistentLevel.Door",
            "component_name": "StaticMeshComponent0",
            "mesh_path": "/Game/Props/SM_Door.SM_Door",
            "material_slots": [{
                "slot_index": 0,
                "slot_name": "Body",
                "current_material_path": "/Game/Materials/M_Default.M_Default"
            }]
        },
        "proposed_by": {"provider": "ollama", "model": "qwen3-embedding:0.6b"},
        "selected_slot": {
            "slot_index": 0,
            "slot_name": "Body",
            "current_material_path": "/Game/Materials/M_Default.M_Default"
        },
        "selected_material": {
            "rank": 1,
            "asset_id": "weathered_planks",
            "display_name": "Weathered Planks",
            "engine_path": "/Game/Materials/M_WeatheredPlanks",
            "similarity": 0.82
        },
        "alternatives": [],
        "rationale": "Best catalog-verified semantic match.",
        "missing_capabilities": [],
        "modifies_editor_scene": true,
        "auto_save": false,
        "undo_supported": true
    })JSON");

    FRenderMasterMaterialProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Material proposal parses"),
        FRenderMasterMaterialProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("Proposal ID"), Proposal.ProposalId, FString(TEXT("proposal_001")));
    TestEqual(TEXT("Material asset ID"), Proposal.MaterialAssetId, FString(TEXT("weathered_planks")));
    TestEqual(TEXT("Material slot"), Proposal.SlotIndex, 0);
    TestEqual(
        TEXT("Material package path"),
        Proposal.MaterialPath,
        FString(TEXT("/Game/Materials/M_WeatheredPlanks")));
    TestTrue(TEXT("Similarity is preserved"), FMath::IsNearlyEqual(Proposal.Similarity, 0.82));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterExternalMaterialProposalParseTest,
    "RenderMasterBot.Editor.Assistant.ExternalMaterialProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterExternalMaterialProposalParseTest::RunTest(const FString& Parameters)
{
    const FString ProposalJson = TEXT(R"JSON({
        "proposal_id": "external_wood_001",
        "status": "pending_approval",
        "provider_credit": "Powered by Poly Haven",
        "provider_asset_id": "wood_planks",
        "display_name": "Wood Planks",
        "description": "Weathered brown boards.",
        "source_url": "https://polyhaven.com/a/wood_planks",
        "license": "CC0-1.0",
        "license_url": "https://polyhaven.com/license",
        "resolution": "1k",
        "image_format": "jpg",
        "import_proposal_path": "C:/RenderBot/external_import_proposal.json",
        "import_proposal_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "destination_path": "/Game/RenderMasterBot/Imported/PolyHaven/WoodPlanks",
        "material_name": "M_PH_WoodPlanks",
        "planned_asset_paths": [
            "/Game/RenderMasterBot/Imported/PolyHaven/WoodPlanks/T_BaseColor",
            "/Game/RenderMasterBot/Imported/PolyHaven/WoodPlanks/T_Normal",
            "/Game/RenderMasterBot/Imported/PolyHaven/WoodPlanks/T_Roughness",
            "/Game/RenderMasterBot/Imported/PolyHaven/WoodPlanks/T_AO",
            "/Game/RenderMasterBot/Imported/PolyHaven/WoodPlanks/M_PH_WoodPlanks"
        ]
    })JSON");

    FRenderMasterExternalMaterialProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("External material proposal parses"),
        FRenderMasterExternalMaterialProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("External provider ID"), Proposal.ProviderAssetId, FString(TEXT("wood_planks")));
    TestEqual(TEXT("External license"), Proposal.License, FString(TEXT("CC0-1.0")));
    TestEqual(TEXT("Exactly five planned assets"), Proposal.PlannedAssetPaths.Num(), 5);
    TestEqual(TEXT("Approval hash length"), Proposal.ImportProposalSha256.Len(), 64);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterTransformProposalParseTest,
    "RenderMasterBot.Editor.Assistant.TransformProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterTransformProposalParseTest::RunTest(const FString& Parameters)
{
    const FString ProposalJson = TEXT(R"JSON({
        "schema_version": "0.1",
        "proposal_id": "transform_001",
        "status": "proposed",
        "request": "Move this Actor up by 50 cm and rotate it right 30 degrees.",
        "target": {
            "schema_version": "0.1",
            "project_name": "OptimizationPlugin",
            "level_path": "/Game/Maps/TestMap",
            "actor_name": "TestCube",
            "actor_path": "/Game/Maps/TestMap.TestMap:PersistentLevel.TestCube",
            "actor_class": "StaticMeshActor",
            "actor_guid": "0123456789ABCDEF0123456789ABCDEF",
            "root_component_name": "StaticMeshComponent0",
            "root_mobility": "movable",
            "is_editable": true,
            "is_locked": false,
            "transform": {
                "location_cm": {"x": 100.0, "y": 200.0, "z": 300.0},
                "rotation_deg": {"x": 0.0, "y": 0.0, "z": 10.0},
                "scale": {"x": 1.0, "y": 1.0, "z": 1.0}
            }
        },
        "proposed_by": {"provider": "ollama", "model": "gpt-oss:20b"},
        "coordinate_space": "world",
        "before": {
            "location_cm": {"x": 100.0, "y": 200.0, "z": 300.0},
            "rotation_deg": {"x": 0.0, "y": 0.0, "z": 10.0},
            "scale": {"x": 1.0, "y": 1.0, "z": 1.0}
        },
        "after": {
            "location_cm": {"x": 100.0, "y": 200.0, "z": 350.0},
            "rotation_deg": {"x": 0.0, "y": 0.0, "z": 40.0},
            "scale": {"x": 1.0, "y": 1.0, "z": 1.0}
        },
        "changes": [
            {
                "channel": "location",
                "operation": "add",
                "axes": ["z"],
                "before": {"x": 100.0, "y": 200.0, "z": 300.0},
                "after": {"x": 100.0, "y": 200.0, "z": 350.0}
            },
            {
                "channel": "rotation",
                "operation": "add",
                "axes": ["z"],
                "before": {"x": 0.0, "y": 0.0, "z": 10.0},
                "after": {"x": 0.0, "y": 0.0, "z": 40.0}
            }
        ],
        "rationale": "Move upward and adjust yaw in world space.",
        "missing_capabilities": [],
        "modifies_editor_scene": true,
        "auto_save": false,
        "undo_supported": true
    })JSON");

    FRenderMasterTransformProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Transform proposal parses"),
        FRenderMasterTransformProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("Transform Actor path"), Proposal.ActorPath, FString(TEXT("/Game/Maps/TestMap.TestMap:PersistentLevel.TestCube")));
    TestTrue(TEXT("Before Z is preserved"), FMath::IsNearlyEqual(Proposal.Before.Location.Z, 300.0));
    TestTrue(TEXT("After Z is preserved"), FMath::IsNearlyEqual(Proposal.After.Location.Z, 350.0));
    TestTrue(TEXT("Yaw mapping is preserved"), FMath::IsNearlyEqual(Proposal.After.Rotation.Yaw, 40.0));
    TestTrue(TEXT("Change summary names location"), Proposal.ChangeSummary.Contains(TEXT("location")));

    FString TamperedJson = ProposalJson.Replace(
        TEXT("\"transform\": {\n                \"location_cm\": {\"x\": 100.0, \"y\": 200.0, \"z\": 300.0}"),
        TEXT("\"transform\": {\n                \"location_cm\": {\"x\": 100.0, \"y\": 200.0, \"z\": 999.0}"),
        ESearchCase::CaseSensitive);
    TestFalse(
        TEXT("Target and before evidence cannot disagree"),
        FRenderMasterTransformProposal::Parse(TamperedJson, Proposal, Error));

    const FString TamperedAxesJson = ProposalJson.Replace(
        TEXT("\"axes\": [\"z\"]"),
        TEXT("\"axes\": [\"x\"]"),
        ESearchCase::CaseSensitive);
    TestFalse(
        TEXT("Changed axes must match the Before and After evidence"),
        FRenderMasterTransformProposal::Parse(TamperedAxesJson, Proposal, Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterTransformApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.TransformApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterTransformApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    FActorSpawnParameters SpawnParameters;
    SpawnParameters.Name = MakeUniqueObjectName(
        World->PersistentLevel,
        AStaticMeshActor::StaticClass(),
        TEXT("RenderMasterTransformUndoTest"));
    SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
    SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(),
        FVector::ZeroVector,
        FRotator::ZeroRotator,
        SpawnParameters);
    if (Actor == nullptr)
    {
        AddError(TEXT("Could not spawn the transient Transform test Actor."));
        return false;
    }

    const FVector BeforeLocation(10.0, 20.0, 30.0);
    Actor->SetActorLocation(BeforeLocation, false, nullptr, ETeleportType::TeleportPhysics);
    FRenderMasterTransformSnapshot After;
    After.Location = FVector(10.0, 20.0, 80.0);
    After.Rotation = FRotator(0.0, 45.0, 0.0);
    After.Scale = FVector(1.0, 1.0, 2.0);
    FString Error;
    const bool bApplied = RenderMasterApplyActorTransform(Actor, After, Error, false);
    TestTrue(TEXT("Approved Transform helper applies"), bApplied);
    TestTrue(TEXT("Applied location matches"), Actor->GetActorLocation().Equals(After.Location, 0.01));
    TestTrue(TEXT("Applied rotation matches"), Actor->GetActorRotation().Equals(After.Rotation, 0.01));
    TestTrue(TEXT("Applied scale matches"), Actor->GetActorScale3D().Equals(After.Scale, 0.0001));

    const bool bUndone = GEditor->UndoTransaction(false);
    TestTrue(TEXT("Unreal transaction can be undone"), bUndone);
    TestTrue(TEXT("Undo restores the original location"), Actor->GetActorLocation().Equals(BeforeLocation, 0.01));
    World->DestroyActor(Actor, false, false);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterTransformBatchProposalParseTest,
    "RenderMasterBot.Editor.Assistant.TransformBatchProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterTransformBatchProposalParseTest::RunTest(const FString& Parameters)
{
    const FString ActorA = TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
        "actor_name":"ActorA","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.ActorA",
        "actor_class":"StaticMeshActor","actor_guid":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "root_component_name":"StaticMeshComponent0","root_mobility":"movable",
        "is_editable":true,"is_locked":false,
        "transform":{"location_cm":{"x":0,"y":0,"z":0},"rotation_deg":{"x":0,"y":0,"z":0},"scale":{"x":1,"y":1,"z":1}}
    })JSON");
    const FString ActorB = TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
        "actor_name":"ActorB","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.ActorB",
        "actor_class":"StaticMeshActor","actor_guid":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "root_component_name":"StaticMeshComponent0","root_mobility":"movable",
        "is_editable":true,"is_locked":false,
        "transform":{"location_cm":{"x":200,"y":0,"z":0},"rotation_deg":{"x":0,"y":0,"z":90},"scale":{"x":1,"y":1,"z":1}}
    })JSON");
    const FString ProposalJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","proposal_id":"transform_batch_001","status":"proposed",
        "request":"Move all selected Actors forward 100 cm in local space.",
        "selection":{"schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap","actors":[%s,%s]},
        "proposed_by":{"provider":"ollama","model":"gpt-oss:20b"},"coordinate_space":"local",
        "actions":[
            {"target":%s,
             "before":{"location_cm":{"x":0,"y":0,"z":0},"rotation_deg":{"x":0,"y":0,"z":0},"scale":{"x":1,"y":1,"z":1}},
             "after":{"location_cm":{"x":100,"y":0,"z":0},"rotation_deg":{"x":0,"y":0,"z":0},"scale":{"x":1,"y":1,"z":1}},
             "changes":[{"channel":"location","operation":"add","axes":["x"],"before":{"x":0,"y":0,"z":0},"after":{"x":100,"y":0,"z":0}}]},
            {"target":%s,
             "before":{"location_cm":{"x":200,"y":0,"z":0},"rotation_deg":{"x":0,"y":0,"z":90},"scale":{"x":1,"y":1,"z":1}},
             "after":{"location_cm":{"x":200,"y":100,"z":0},"rotation_deg":{"x":0,"y":0,"z":90},"scale":{"x":1,"y":1,"z":1}},
             "changes":[{"channel":"location","operation":"add","axes":["y"],"before":{"x":200,"y":0,"z":0},"after":{"x":200,"y":100,"z":0}}]}
        ],
        "rationale":"Apply the same local-forward offset using each Actor basis.",
        "missing_capabilities":[],"modifies_editor_scene":true,"auto_save":false,"undo_supported":true
    })JSON"), *ActorA, *ActorB, *ActorA, *ActorB);

    FRenderMasterTransformBatchProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Complete local-space batch proposal parses"),
        FRenderMasterTransformBatchProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("Batch preserves two ordered Actor actions"), Proposal.Actions.Num(), 2);
    TestEqual(TEXT("Batch coordinate space is local"), Proposal.CoordinateSpace, FString(TEXT("local")));
    TestTrue(TEXT("Second Actor local basis produces world Y movement"),
        FMath::IsNearlyEqual(Proposal.Actions[1].After.Location.Y, 100.0));

    FString TamperedJson = ProposalJson;
    const FString OriginalName = TEXT("\"actor_name\":\"ActorA\"");
    const int32 FirstName = TamperedJson.Find(OriginalName, ESearchCase::CaseSensitive);
    if (FirstName != INDEX_NONE)
    {
        TamperedJson.RemoveAt(FirstName, OriginalName.Len(), EAllowShrinking::No);
        TamperedJson.InsertAt(FirstName, TEXT("\"actor_name\":\"SpoofedActor\""));
    }
    TestFalse(
        TEXT("Action target must match the frozen ordered selection"),
        FRenderMasterTransformBatchProposal::Parse(TamperedJson, Proposal, Error));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterTransformBatchApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.TransformBatchApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterTransformBatchApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    TArray<AActor*> Actors;
    for (int32 Index = 0; Index < 2; ++Index)
    {
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = MakeUniqueObjectName(
            World->PersistentLevel,
            AStaticMeshActor::StaticClass(),
            *FString::Printf(TEXT("RenderMasterTransformBatchUndoTest%d"), Index));
        SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
        SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(
            AStaticMeshActor::StaticClass(),
            FVector(Index * 200.0, 0.0, 0.0),
            FRotator::ZeroRotator,
            SpawnParameters);
        if (Actor == nullptr)
        {
            AddError(TEXT("Could not spawn a transient batch Transform test Actor."));
            for (AActor* Spawned : Actors) World->DestroyActor(Spawned, false, false);
            return false;
        }
        Actors.Add(Actor);
    }

    TArray<FRenderMasterTransformSnapshot> Before;
    TArray<FRenderMasterTransformSnapshot> After;
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        FRenderMasterTransformSnapshot Snapshot;
        Snapshot.Location = Actors[Index]->GetActorLocation();
        Snapshot.Rotation = Actors[Index]->GetActorRotation();
        Snapshot.Scale = Actors[Index]->GetActorScale3D();
        Before.Add(Snapshot);
        Snapshot.Location += FVector(0.0, 100.0 + Index * 50.0, 25.0);
        After.Add(Snapshot);
    }

    FString Error;
    TestTrue(
        TEXT("Grouped batch Transform helper applies"),
        RenderMasterApplyActorTransformBatch(Actors, Before, After, Error, false));
    TestTrue(TEXT("First Actor receives its proposed Transform"),
        Actors[0]->GetActorLocation().Equals(After[0].Location, 0.01));
    TestTrue(TEXT("Second Actor receives its proposed Transform"),
        Actors[1]->GetActorLocation().Equals(After[1].Location, 0.01));

    TestTrue(TEXT("One Unreal transaction undoes the complete batch"), GEditor->UndoTransaction(false));
    TestTrue(TEXT("Undo restores first Actor"),
        Actors[0]->GetActorLocation().Equals(Before[0].Location, 0.01));
    TestTrue(TEXT("Undo restores second Actor"),
        Actors[1]->GetActorLocation().Equals(Before[1].Location, 0.01));
    for (AActor* Actor : Actors) World->DestroyActor(Actor, false, false);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightProposalParseTest,
    "RenderMasterBot.Editor.Assistant.LightProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightProposalParseTest::RunTest(const FString& Parameters)
{
    const FString ProposalJson = TEXT(R"JSON({
        "schema_version": "0.1",
        "proposal_id": "light_001",
        "status": "proposed",
        "request": "Make this light 20 percent brighter and 3200K.",
        "target": {
            "schema_version": "0.1",
            "project_name": "OptimizationPlugin",
            "level_path": "/Game/Maps/TestMap",
            "actor_name": "KeyLight",
            "actor_path": "/Game/Maps/TestMap.TestMap:PersistentLevel.KeyLight",
            "actor_class": "SpotLight",
            "actor_guid": "0123456789ABCDEF0123456789ABCDEF",
            "component_name": "LightComponent0",
            "light_kind": "spot",
            "component_mobility": "movable",
            "is_editable": true,
            "is_locked": false,
            "light": {
                "rotation_deg": {"x": 0.0, "y": -20.0, "z": 10.0},
                "intensity": 5000.0,
                "intensity_unit": "lumens",
                "color_rgb": {"r": 1.0, "g": 1.0, "b": 1.0},
                "use_temperature": false,
                "temperature_kelvin": 6500.0,
                "cast_shadows": true,
                "attenuation_radius_cm": 1000.0,
                "inner_cone_deg": 20.0,
                "outer_cone_deg": 40.0
            }
        },
        "proposed_by": {"provider": "ollama", "model": "gpt-oss:20b"},
        "before": {
            "rotation_deg": {"x": 0.0, "y": -20.0, "z": 10.0},
            "intensity": 5000.0,
            "intensity_unit": "lumens",
            "color_rgb": {"r": 1.0, "g": 1.0, "b": 1.0},
            "use_temperature": false,
            "temperature_kelvin": 6500.0,
            "cast_shadows": true,
            "attenuation_radius_cm": 1000.0,
            "inner_cone_deg": 20.0,
            "outer_cone_deg": 40.0
        },
        "after": {
            "rotation_deg": {"x": 0.0, "y": -20.0, "z": 40.0},
            "intensity": 6000.0,
            "intensity_unit": "lumens",
            "color_rgb": {"r": 1.0, "g": 1.0, "b": 1.0},
            "use_temperature": true,
            "temperature_kelvin": 3200.0,
            "cast_shadows": true,
            "attenuation_radius_cm": 1000.0,
            "inner_cone_deg": 20.0,
            "outer_cone_deg": 45.0
        },
        "changes": [
            {"property": "intensity", "operation": "multiply"},
            {"property": "temperature_kelvin", "operation": "set"},
            {"property": "use_temperature", "operation": "set"},
            {"property": "outer_cone_deg", "operation": "add"},
            {"property": "rotation", "operation": "add"}
        ],
        "rationale": "Increase and warm the selected Spot Light.",
        "missing_capabilities": [],
        "modifies_editor_scene": true,
        "auto_save": false,
        "undo_supported": true
    })JSON");

    FRenderMasterLightProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Light proposal parses"),
        FRenderMasterLightProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("Light kind"), Proposal.LightKind, FString(TEXT("spot")));
    TestTrue(TEXT("Before intensity preserved"), FMath::IsNearlyEqual(Proposal.Before.Intensity, 5000.0));
    TestTrue(TEXT("After intensity preserved"), FMath::IsNearlyEqual(Proposal.After.Intensity, 6000.0));
    TestTrue(TEXT("After temperature enabled"), Proposal.After.bUseTemperature);
    TestTrue(TEXT("Change summary contains rotation"), Proposal.ChangeSummary.Contains(TEXT("rotation")));

    const FString TamperedJson = ProposalJson.Replace(
        TEXT("{\"property\": \"rotation\", \"operation\": \"add\"}"),
        TEXT("{\"property\": \"cast_shadows\", \"operation\": \"set\"}"),
        ESearchCase::CaseSensitive);
    TestFalse(
        TEXT("Change list must match Before and After evidence"),
        FRenderMasterLightProposal::Parse(TamperedJson, Proposal, Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.LightApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    FActorSpawnParameters SpawnParameters;
    SpawnParameters.Name = MakeUniqueObjectName(
        World->PersistentLevel,
        APointLight::StaticClass(),
        TEXT("RenderMasterLightUndoTest"));
    SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
    SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    APointLight* Actor = World->SpawnActor<APointLight>(
        APointLight::StaticClass(),
        FVector::ZeroVector,
        FRotator::ZeroRotator,
        SpawnParameters);
    if (Actor == nullptr)
    {
        AddError(TEXT("Could not spawn the transient light test Actor."));
        return false;
    }
    UPointLightComponent* Component = Actor->PointLightComponent;
    Component->SetIntensityUnits(ELightUnits::Lumens);
    Component->SetIntensity(5000.0f);
    Component->SetLightColor(FLinearColor::White, false);
    Component->SetUseTemperature(false);
    Component->SetTemperature(6500.0f);
    Component->SetCastShadows(true);
    Component->SetAttenuationRadius(1000.0f);

    FRenderMasterLightSnapshot Before;
    Before.Rotation = Actor->GetActorRotation();
    Before.Intensity = 5000.0;
    Before.IntensityUnit = TEXT("lumens");
    Before.Color = FLinearColor::White;
    Before.bUseTemperature = false;
    Before.TemperatureKelvin = 6500.0;
    Before.bCastShadows = true;
    Before.AttenuationRadiusCm = 1000.0;

    FRenderMasterLightSnapshot After = Before;
    After.Intensity = 6000.0;
    After.Color = FLinearColor(0.2f, 0.4f, 1.0f);
    After.bUseTemperature = true;
    After.TemperatureKelvin = 3200.0;
    After.bCastShadows = false;
    After.AttenuationRadiusCm = 1500.0;

    FString Error;
    const bool bApplied = RenderMasterApplyLightProperties(
        Actor, Component, Before, After, Error, false);
    TestTrue(TEXT("Approved light helper applies"), bApplied);
    TestTrue(TEXT("Applied intensity matches"), FMath::IsNearlyEqual(Component->Intensity, 6000.0f));
    TestTrue(TEXT("Applied color matches"), Component->GetLightColor().Equals(After.Color, 0.005f));
    TestTrue(TEXT("Applied temperature state matches"), Component->bUseTemperature && FMath::IsNearlyEqual(Component->Temperature, 3200.0f));
    TestFalse(TEXT("Applied shadow state matches"), Component->CastShadows);
    TestTrue(TEXT("Applied attenuation matches"), FMath::IsNearlyEqual(Component->AttenuationRadius, 1500.0f));

    const bool bUndone = GEditor->UndoTransaction(false);
    TestTrue(TEXT("Light transaction can be undone"), bUndone);
    TestTrue(TEXT("Undo restores intensity"), FMath::IsNearlyEqual(Component->Intensity, 5000.0f));
    TestFalse(TEXT("Undo restores temperature toggle"), Component->bUseTemperature);
    TestTrue(TEXT("Undo restores attenuation"), FMath::IsNearlyEqual(Component->AttenuationRadius, 1000.0f));
    World->DestroyActor(Actor, false, false);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightBatchProposalParseTest,
    "RenderMasterBot.Editor.Assistant.LightBatchProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightBatchProposalParseTest::RunTest(const FString& Parameters)
{
    const FString LightA = TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
        "actor_name":"FillA","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.FillA",
        "actor_class":"PointLight","actor_guid":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "component_name":"PointLightComponent0","light_kind":"point","component_mobility":"movable",
        "is_editable":true,"is_locked":false,
        "light":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":5000,"intensity_unit":"lumens",
        "color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,
        "cast_shadows":true,"attenuation_radius_cm":1000,"inner_cone_deg":null,"outer_cone_deg":null}
    })JSON");
    const FString LightB = TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
        "actor_name":"FillB","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.FillB",
        "actor_class":"PointLight","actor_guid":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "component_name":"PointLightComponent0","light_kind":"point","component_mobility":"movable",
        "is_editable":true,"is_locked":false,
        "light":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":8000,"intensity_unit":"lumens",
        "color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,
        "cast_shadows":true,"attenuation_radius_cm":1200,"inner_cone_deg":null,"outer_cone_deg":null}
    })JSON");
    const FString ProposalJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","proposal_id":"light_batch_001","status":"proposed",
        "request":"Make all selected lights 20 percent brighter.",
        "selection":{"schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap","lights":[%s,%s]},
        "proposed_by":{"provider":"ollama","model":"gpt-oss:20b"},
        "actions":[
            {"target":%s,
             "before":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":5000,"intensity_unit":"lumens","color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,"cast_shadows":true,"attenuation_radius_cm":1000,"inner_cone_deg":null,"outer_cone_deg":null},
             "after":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":6000,"intensity_unit":"lumens","color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,"cast_shadows":true,"attenuation_radius_cm":1000,"inner_cone_deg":null,"outer_cone_deg":null},
             "changes":[{"property":"intensity","operation":"multiply"}]},
            {"target":%s,
             "before":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":8000,"intensity_unit":"lumens","color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,"cast_shadows":true,"attenuation_radius_cm":1200,"inner_cone_deg":null,"outer_cone_deg":null},
             "after":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":9600,"intensity_unit":"lumens","color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,"cast_shadows":true,"attenuation_radius_cm":1200,"inner_cone_deg":null,"outer_cone_deg":null},
             "changes":[{"property":"intensity","operation":"multiply"}]}
        ],
        "rationale":"Apply one relative brightness change to the complete selection.",
        "missing_capabilities":[],"modifies_editor_scene":true,"auto_save":false,"undo_supported":true
    })JSON"), *LightA, *LightB, *LightA, *LightB);

    FRenderMasterLightBatchProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Complete batch light proposal parses"),
        FRenderMasterLightBatchProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("Batch preserves two ordered light actions"), Proposal.Actions.Num(), 2);
    TestTrue(TEXT("First light After intensity is preserved"),
        FMath::IsNearlyEqual(Proposal.Actions[0].After.Intensity, 6000.0));
    TestTrue(TEXT("Second light After intensity is preserved"),
        FMath::IsNearlyEqual(Proposal.Actions[1].After.Intensity, 9600.0));

    FString TamperedJson = ProposalJson;
    const FString OriginalName = TEXT("\"actor_name\":\"FillA\"");
    const int32 FirstName = TamperedJson.Find(OriginalName, ESearchCase::CaseSensitive);
    if (FirstName != INDEX_NONE)
    {
        TamperedJson.RemoveAt(FirstName, OriginalName.Len(), EAllowShrinking::No);
        TamperedJson.InsertAt(FirstName, TEXT("\"actor_name\":\"SpoofedFill\""));
    }
    TestFalse(
        TEXT("Batch action target must match the frozen ordered light selection"),
        FRenderMasterLightBatchProposal::Parse(TamperedJson, Proposal, Error));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightBatchApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.LightBatchApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightBatchApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    TArray<ALight*> Actors;
    TArray<ULightComponent*> Components;
    TArray<FRenderMasterLightSnapshot> Before;
    TArray<FRenderMasterLightSnapshot> After;
    for (int32 Index = 0; Index < 2; ++Index)
    {
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = MakeUniqueObjectName(
            World->PersistentLevel,
            APointLight::StaticClass(),
            *FString::Printf(TEXT("RenderMasterLightBatchUndoTest%d"), Index));
        SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
        SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        APointLight* Actor = World->SpawnActor<APointLight>(
            APointLight::StaticClass(),
            FVector(Index * 100.0, 0.0, 0.0),
            FRotator::ZeroRotator,
            SpawnParameters);
        if (Actor == nullptr)
        {
            AddError(TEXT("Could not spawn a transient batch light test Actor."));
            for (ALight* Spawned : Actors) World->DestroyActor(Spawned, false, false);
            return false;
        }
        UPointLightComponent* Component = Actor->PointLightComponent;
        const double Intensity = 5000.0 + Index * 3000.0;
        Component->SetIntensityUnits(ELightUnits::Lumens);
        Component->SetIntensity(Intensity);
        Component->SetLightColor(FLinearColor::White, false);
        Component->SetUseTemperature(false);
        Component->SetTemperature(6500.0f);
        Component->SetCastShadows(true);
        Component->SetAttenuationRadius(1000.0f + Index * 200.0f);

        FRenderMasterLightSnapshot Snapshot;
        Snapshot.Rotation = Actor->GetActorRotation();
        Snapshot.Intensity = Intensity;
        Snapshot.IntensityUnit = TEXT("lumens");
        Snapshot.Color = FLinearColor::White;
        Snapshot.bUseTemperature = false;
        Snapshot.TemperatureKelvin = 6500.0;
        Snapshot.bCastShadows = true;
        Snapshot.AttenuationRadiusCm = 1000.0 + Index * 200.0;
        Actors.Add(Actor);
        Components.Add(Component);
        Before.Add(Snapshot);
        Snapshot.Intensity *= 1.2;
        Snapshot.TemperatureKelvin = 4500.0;
        Snapshot.bUseTemperature = true;
        After.Add(Snapshot);
    }

    FString Error;
    TestTrue(
        TEXT("Grouped batch light helper applies"),
        RenderMasterApplyLightPropertiesBatch(Actors, Components, Before, After, Error, false));
    TestTrue(TEXT("First light receives proposed intensity"),
        FMath::IsNearlyEqual(Components[0]->Intensity, 6000.0f));
    TestTrue(TEXT("Second light receives proposed intensity"),
        FMath::IsNearlyEqual(Components[1]->Intensity, 9600.0f));
    TestTrue(TEXT("Both lights receive temperature mode"),
        Components[0]->bUseTemperature && Components[1]->bUseTemperature);

    TestTrue(TEXT("One Unreal transaction undoes the complete light group"),
        GEditor->UndoTransaction(false));
    TestTrue(TEXT("Undo restores first light"),
        FMath::IsNearlyEqual(Components[0]->Intensity, 5000.0f));
    TestTrue(TEXT("Undo restores second light"),
        FMath::IsNearlyEqual(Components[1]->Intensity, 8000.0f));
    TestFalse(TEXT("Undo restores first temperature toggle"), Components[0]->bUseTemperature);
    TestFalse(TEXT("Undo restores second temperature toggle"), Components[1]->bUseTemperature);
    for (ALight* Actor : Actors) World->DestroyActor(Actor, false, false);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightingRigProposalParseTest,
    "RenderMasterBot.Editor.Assistant.LightingRigProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightingRigProposalParseTest::RunTest(const FString& Parameters)
{
    const auto MakeLight = [](const TCHAR* Name, const TCHAR* Guid, double Intensity)
    {
        return FString::Printf(TEXT(R"JSON({
            "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
            "actor_name":"%s","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.%s",
            "actor_class":"PointLight","actor_guid":"%s","component_name":"PointLightComponent0",
            "light_kind":"point","component_mobility":"movable","is_editable":true,"is_locked":false,
            "light":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":%.1f,"intensity_unit":"lumens",
            "color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,
            "cast_shadows":true,"attenuation_radius_cm":1000,"inner_cone_deg":null,"outer_cone_deg":null}
            })JSON"), Name, Name, Guid, Intensity);
    };
    const auto MakeRigLight = [](const FString& Light, double X, double Y, double Z)
    {
        return FString::Printf(
            TEXT("{\"target\":%s,\"location_cm\":{\"x\":%.1f,\"y\":%.1f,\"z\":%.1f}}"),
            *Light, X, Y, Z);
    };
    const auto MakeState = [](double X, double Y, double Z, double Intensity,
        bool bTemperature, double Temperature, double Attenuation)
    {
        return FString::Printf(TEXT(R"JSON({
            "location_cm":{"x":%.1f,"y":%.1f,"z":%.1f},
            "light":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":%.1f,"intensity_unit":"lumens",
            "color_rgb":{"r":1,"g":1,"b":1},"use_temperature":%s,"temperature_kelvin":%.1f,
            "cast_shadows":true,"attenuation_radius_cm":%.1f,"inner_cone_deg":null,"outer_cone_deg":null}
        })JSON"), X, Y, Z, Intensity, bTemperature ? TEXT("true") : TEXT("false"),
            Temperature, Attenuation);
    };

    const FString LightA = MakeLight(TEXT("RigA"), TEXT("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"), 5000.0);
    const FString LightB = MakeLight(TEXT("RigB"), TEXT("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"), 3000.0);
    const FString LightC = MakeLight(TEXT("RigC"), TEXT("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"), 2500.0);
    const FString RigA = MakeRigLight(LightA, -200.0, -300.0, 300.0);
    const FString RigB = MakeRigLight(LightB, -200.0, 300.0, 250.0);
    const FString RigC = MakeRigLight(LightC, 200.0, 0.0, 300.0);
    const FString BeforeA = MakeState(-200.0, -300.0, 300.0, 5000.0, false, 6500.0, 1000.0);
    const FString BeforeB = MakeState(-200.0, 300.0, 250.0, 3000.0, false, 6500.0, 1000.0);
    const FString BeforeC = MakeState(200.0, 0.0, 300.0, 2500.0, false, 6500.0, 1000.0);
    const FString AfterA = MakeState(-350.0, -350.0, 400.0, 5000.0, true, 4200.0, 1500.0);
    const FString AfterB = MakeState(-300.0, 350.0, 250.0, 2000.0, true, 5000.0, 1500.0);
    const FString AfterC = MakeState(350.0, 150.0, 400.0, 3250.0, true, 7000.0, 1500.0);
    const FString Changes = TEXT(R"JSON([
        {"property":"location","operation":"set"},
        {"property":"intensity","operation":"set"},
        {"property":"use_temperature","operation":"set"},
        {"property":"temperature_kelvin","operation":"set"},
        {"property":"attenuation_radius_cm","operation":"set"}
    ])JSON");
    const FString KeyChanges = TEXT(R"JSON([
        {"property":"location","operation":"set"},
        {"property":"use_temperature","operation":"set"},
        {"property":"temperature_kelvin","operation":"set"},
        {"property":"attenuation_radius_cm","operation":"set"}
    ])JSON");

    const FString ProposalJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","proposal_id":"rig_001","status":"proposed",
        "request":"Create a warm cinematic three-point rig.",
        "context":{"schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
            "subject":{"actor_name":"Subject","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.Subject",
                "actor_class":"StaticMeshActor","actor_guid":"11111111111111111111111111111111",
                "root_component_name":"StaticMeshComponent0","root_mobility":"movable","is_editable":true,"is_locked":false,
                "transform":{"location_cm":{"x":0,"y":0,"z":100},"rotation_deg":{"x":0,"y":0,"z":0},"scale":{"x":1,"y":1,"z":1}},
                "bounds":{"center_cm":{"x":0,"y":0,"z":100},"extent_cm":{"x":80,"y":60,"z":100},"sphere_radius_cm":141.421356}},
            "camera":{"actor_name":"Camera","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.Camera",
                "actor_class":"CameraActor","actor_guid":"22222222222222222222222222222222","component_name":"CameraComponent",
                "camera_kind":"camera","component_mobility":"movable","projection_mode":"perspective","is_editable":true,"is_locked":false,
                "location_cm":{"x":-700,"y":0,"z":150},"rotation_deg":{"x":0,"y":-4.0856,"z":0}},
            "lights":[%s,%s,%s]},
        "proposed_by":{"provider":"ollama","model":"gpt-oss:20b"},
        "contrast":"balanced","palette":"warm_cool","key_side":"camera_left","spacing":"standard","brightness":"balanced",
        "actions":[
            {"role":"key","target":%s,"before":%s,"after":%s,"changes":%s},
            {"role":"fill","target":%s,"before":%s,"after":%s,"changes":%s},
            {"role":"rim","target":%s,"before":%s,"after":%s,"changes":%s}],
        "rationale":"Use bounded camera-relative three-point placement.","missing_capabilities":[],
        "modifies_editor_scene":true,"auto_save":false,"undo_supported":true
    })JSON"),
        *RigA, *RigB, *RigC,
        *RigA, *BeforeA, *AfterA, *KeyChanges,
        *RigB, *BeforeB, *AfterB, *Changes,
        *RigC, *BeforeC, *AfterC, *Changes);

    FRenderMasterLightingRigProposal Proposal;
    FString Error;
    const bool bParsed = FRenderMasterLightingRigProposal::Parse(
        ProposalJson, Proposal, Error);
    TestTrue(
        *FString::Printf(TEXT("Lighting-rig proposal parses: %s"), *Error),
        bParsed);
    if (bParsed)
    {
        TestEqual(TEXT("Rig contains three actions"), Proposal.Actions.Num(), 3);
        TestEqual(TEXT("First action is Key"), Proposal.Actions[0].Role, FString(TEXT("key")));
        TestTrue(TEXT("Fill intensity is preserved"),
            FMath::IsNearlyEqual(Proposal.Actions[1].After.Light.Intensity, 2000.0));
    }

    FString TamperedTarget = ProposalJson;
    const FString OriginalName = TEXT("\"actor_name\":\"RigA\"");
    const int32 FirstName = TamperedTarget.Find(OriginalName, ESearchCase::CaseSensitive);
    if (FirstName != INDEX_NONE)
    {
        TamperedTarget.RemoveAt(FirstName, OriginalName.Len(), EAllowShrinking::No);
        TamperedTarget.InsertAt(FirstName, TEXT("\"actor_name\":\"SpoofedRigA\""));
    }
    TestFalse(
        TEXT("Rig action target must match ordered frozen context"),
        FRenderMasterLightingRigProposal::Parse(TamperedTarget, Proposal, Error));

    const FString DuplicateRole = ProposalJson.Replace(
        TEXT("\"role\":\"key\""),
        TEXT("\"role\":\"fill\""),
        ESearchCase::CaseSensitive);
    TestFalse(
        TEXT("Rig roles must contain Key, Fill, and Rim exactly once"),
        FRenderMasterLightingRigProposal::Parse(DuplicateRole, Proposal, Error));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightingRigReviewProposalParseTest,
    "RenderMasterBot.Editor.Assistant.LightingRigReviewProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightingRigReviewProposalParseTest::RunTest(
    const FString& Parameters)
{
    const auto MakeLight = [](const TCHAR* Name, const TCHAR* Guid, double Intensity)
    {
        return FString::Printf(TEXT(R"JSON({
            "target":{"schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
                "actor_name":"%s","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.%s",
                "actor_class":"PointLight","actor_guid":"%s","component_name":"PointLightComponent0",
                "light_kind":"point","component_mobility":"movable","is_editable":true,"is_locked":false,
                "light":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":%.1f,"intensity_unit":"lumens",
                    "color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,
                    "cast_shadows":true,"attenuation_radius_cm":1000,"inner_cone_deg":null,"outer_cone_deg":null}},
            "location_cm":{"x":-200,"y":0,"z":300}
        })JSON"), Name, Name, Guid, Intensity);
    };
    const auto MakeReviewState = [](double Intensity)
    {
        return FString::Printf(TEXT(R"JSON({
            "location_cm":{"x":-200,"y":0,"z":300},
            "light":{"rotation_deg":{"x":0,"y":0,"z":0},"intensity":%.1f,"intensity_unit":"lumens",
                "color_rgb":{"r":1,"g":1,"b":1},"use_temperature":false,"temperature_kelvin":6500,
                "cast_shadows":true,"attenuation_radius_cm":1000,"inner_cone_deg":null,"outer_cone_deg":null}
        })JSON"), Intensity);
    };
    const auto MakeAction = [&MakeReviewState](
        const TCHAR* Role,
        const FString& Target,
        double BeforeIntensity,
        double AfterIntensity)
    {
        return FString::Printf(TEXT(R"JSON({
            "role":"%s","target":%s,"before":%s,"after":%s,
            "changes":[{"property":"intensity","operation":"set"}]
        })JSON"), Role, *Target,
            *MakeReviewState(BeforeIntensity), *MakeReviewState(AfterIntensity));
    };

    const FString LightA = MakeLight(
        TEXT("RigA"), TEXT("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"), 5000.0);
    const FString LightB = MakeLight(
        TEXT("RigB"), TEXT("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"), 3000.0);
    const FString LightC = MakeLight(
        TEXT("RigC"), TEXT("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"), 2500.0);
    const FString ProposalJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","proposal_id":"lighting_review_001","status":"pass",
        "request":"Review the applied three-point lighting against the original request.",
        "context":{"schema_version":"0.1","source_request":"Create a warm cinematic three-point rig.",
            "rig":{"schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
                "subject":{"actor_name":"Subject","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.Subject",
                    "actor_class":"StaticMeshActor","actor_guid":"11111111111111111111111111111111",
                    "root_component_name":"StaticMeshComponent0","root_mobility":"movable","is_editable":true,"is_locked":false,
                    "transform":{"location_cm":{"x":0,"y":0,"z":100},"rotation_deg":{"x":0,"y":0,"z":0},"scale":{"x":1,"y":1,"z":1}},
                    "bounds":{"center_cm":{"x":0,"y":0,"z":100},"extent_cm":{"x":80,"y":60,"z":100},"sphere_radius_cm":141.421356}},
                "camera":{"actor_name":"Camera","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.Camera",
                    "actor_class":"CameraActor","actor_guid":"22222222222222222222222222222222","component_name":"CameraComponent",
                    "camera_kind":"camera","component_mobility":"movable","projection_mode":"perspective","is_editable":true,"is_locked":false,
                    "location_cm":{"x":-700,"y":0,"z":150},"rotation_deg":{"x":0,"y":-4.0856,"z":0}},
                "lights":[%s,%s,%s]},
            "assignments":[
                {"actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.RigA","role":"key"},
                {"actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.RigB","role":"fill"},
                {"actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.RigC","role":"rim"}]},
        "proposed_by":{"provider":"ollama","model":"qwen3-vl:8b-instruct"},
        "preview":{"sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "width_px":1280,"height_px":720,"sampled_pixels":65536,"mean_luminance":0.42,"luminance_stddev":0.18,
            "dark_pixel_fraction":0.2,"clipped_pixel_fraction":0.01,"foreground_fraction":0.4,
            "center_luminance":0.5,"border_luminance":0.2,"blank_like":false,"underexposed_like":false,"overexposed_like":false},
        "exposure":"balanced","fill_balance":"balanced","rim_separation":"balanced","confidence":0.91,
        "summary":"The applied rig is visibly balanced.","actions":[],
        "rationale":"Exposure, shadow detail, and rim separation are balanced.","missing_capabilities":[],
        "modifies_editor_scene":true,"auto_save":false,"undo_supported":true
    })JSON"), *LightA, *LightB, *LightC);

    FRenderMasterLightingRigReviewProposal Proposal;
    FString Error;
    TestTrue(
        *FString::Printf(TEXT("Balanced lighting-rig review parses: %s"), *Error),
        FRenderMasterLightingRigReviewProposal::Parse(ProposalJson, Proposal, Error));
    TestEqual(TEXT("Review status is pass"), Proposal.Status, FString(TEXT("pass")));
    TestEqual(TEXT("Review preserves three context lights"), Proposal.ContextLights.Num(), 3);
    TestEqual(TEXT("Review pass contains no correction"), Proposal.Actions.Num(), 0);

    const FString Actions = FString::Printf(
        TEXT("[%s,%s,%s]"),
        *MakeAction(TEXT("key"), LightA, 5000.0, 6000.0),
        *MakeAction(TEXT("fill"), LightB, 3000.0, 4320.0),
        *MakeAction(TEXT("rim"), LightC, 2500.0, 3600.0));
    const FString ProposedReview = ProposalJson.Replace(
        TEXT("\"status\":\"pass\""),
        TEXT("\"status\":\"proposed\""),
        ESearchCase::CaseSensitive).Replace(
        TEXT("\"exposure\":\"balanced\""),
        TEXT("\"exposure\":\"too_dark\""),
        ESearchCase::CaseSensitive).Replace(
        TEXT("\"actions\":[]"),
        *FString::Printf(TEXT("\"actions\":%s"), *Actions),
        ESearchCase::CaseSensitive);
    Error.Reset();
    TestTrue(
        *FString::Printf(TEXT("Intensity-only review proposal parses: %s"), *Error),
        FRenderMasterLightingRigReviewProposal::Parse(
            ProposedReview, Proposal, Error));
    TestEqual(TEXT("Review proposal contains three corrections"), Proposal.Actions.Num(), 3);
    if (Proposal.Actions.Num() == 3)
    {
        TestTrue(
            TEXT("Review proposal preserves deterministic Fill intensity"),
            FMath::IsNearlyEqual(Proposal.Actions[1].After.Light.Intensity, 4320.0));
    }

    const FString BlankPass = ProposalJson.Replace(
        TEXT("\"blank_like\":false"),
        TEXT("\"blank_like\":true"),
        ESearchCase::CaseSensitive);
    TestFalse(
        TEXT("A blank camera frame cannot pass visual review"),
        FRenderMasterLightingRigReviewProposal::Parse(BlankPass, Proposal, Error));

    const FString MissingActions = ProposalJson.Replace(
        TEXT("\"status\":\"pass\""),
        TEXT("\"status\":\"proposed\""),
        ESearchCase::CaseSensitive).Replace(
        TEXT("\"fill_balance\":\"balanced\""),
        TEXT("\"fill_balance\":\"too_weak\""),
        ESearchCase::CaseSensitive);
    TestFalse(
        TEXT("A proposed correction must carry exactly three bounded actions"),
        FRenderMasterLightingRigReviewProposal::Parse(MissingActions, Proposal, Error));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterLightingRigApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.LightingRigApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterLightingRigApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    TArray<ALight*> Actors;
    TArray<ULightComponent*> Components;
    TArray<FRenderMasterRigLightState> Before;
    TArray<FRenderMasterRigLightState> After;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = MakeUniqueObjectName(
            World->PersistentLevel,
            APointLight::StaticClass(),
            *FString::Printf(TEXT("RenderMasterLightingRigUndoTest%d"), Index));
        SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
        SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        const FVector Location(-200.0 + Index * 200.0, -300.0 + Index * 300.0, 250.0);
        APointLight* Actor = World->SpawnActor<APointLight>(
            APointLight::StaticClass(), Location, FRotator::ZeroRotator, SpawnParameters);
        if (Actor == nullptr)
        {
            AddError(TEXT("Could not spawn a transient lighting-rig test Actor."));
            for (ALight* Spawned : Actors) World->DestroyActor(Spawned, false, false);
            return false;
        }
        UPointLightComponent* Component = Actor->PointLightComponent;
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetIntensityUnits(ELightUnits::Lumens);
        Component->SetIntensity(5000.0f - Index * 1000.0f);
        Component->SetUseTemperature(false);
        Component->SetTemperature(6500.0f);
        Component->SetAttenuationRadius(1000.0f);

        FRenderMasterRigLightState Current;
        Current.Location = Actor->GetActorLocation();
        Current.Light = RenderMasterSnapshotLight(Actor, Component);
        FRenderMasterRigLightState Proposed = Current;
        Proposed.Location += FVector(100.0 * (Index + 1), 50.0 * (Index + 1), 75.0);
        Proposed.Light.Intensity = 6000.0 - Index * 1500.0;
        Proposed.Light.bUseTemperature = true;
        Proposed.Light.TemperatureKelvin = 4200.0 + Index * 1000.0;
        Proposed.Light.AttenuationRadiusCm = 1600.0 + Index * 100.0;
        Actors.Add(Actor);
        Components.Add(Component);
        Before.Add(Current);
        After.Add(Proposed);
    }

    FString Error;
    TestTrue(
        *FString::Printf(TEXT("Grouped lighting-rig helper applies: %s"), *Error),
        RenderMasterApplyLightingRig(Actors, Components, Before, After, Error, false));
    for (int32 Index = 0; Index < 3; ++Index)
    {
        TestTrue(
            *FString::Printf(TEXT("Rig light %d receives proposed location"), Index),
            Actors[Index]->GetActorLocation().Equals(After[Index].Location, 0.01));
        TestTrue(
            *FString::Printf(TEXT("Rig light %d receives proposed intensity"), Index),
            FMath::IsNearlyEqual(Components[Index]->Intensity, After[Index].Light.Intensity));
    }

    TestTrue(TEXT("One Unreal transaction undoes the complete lighting rig"),
        GEditor->UndoTransaction(false));
    for (int32 Index = 0; Index < 3; ++Index)
    {
        TestTrue(
            *FString::Printf(TEXT("Undo restores rig light %d location"), Index),
            Actors[Index]->GetActorLocation().Equals(Before[Index].Location, 0.01));
        TestTrue(
            *FString::Printf(TEXT("Undo restores rig light %d intensity"), Index),
            FMath::IsNearlyEqual(Components[Index]->Intensity, Before[Index].Light.Intensity));
        TestFalse(
            *FString::Printf(TEXT("Undo restores rig light %d temperature toggle"), Index),
            Components[Index]->bUseTemperature);
    }
    for (ALight* Actor : Actors) World->DestroyActor(Actor, false, false);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterCameraProposalParseTest,
    "RenderMasterBot.Editor.Assistant.CameraProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterCameraProposalParseTest::RunTest(const FString& Parameters)
{
    const FString ProposalJson = TEXT(R"JSON({
        "schema_version": "0.1",
        "proposal_id": "camera_001",
        "status": "proposed",
        "request": "Move the camera up 100 cm, set 75 degree FOV, f/5.6, focus at 350 cm, and add 1 EV.",
        "target": {
            "schema_version": "0.1",
            "project_name": "OptimizationPlugin",
            "level_path": "/Game/Maps/TestMap",
            "actor_name": "ProductCamera",
            "actor_path": "/Game/Maps/TestMap.TestMap:PersistentLevel.ProductCamera",
            "actor_class": "CameraActor",
            "actor_guid": "0123456789ABCDEF0123456789ABCDEF",
            "component_name": "CameraComponent",
            "camera_kind": "camera",
            "component_mobility": "movable",
            "projection_mode": "perspective",
            "is_editable": true,
            "is_locked": false,
            "min_focal_length_mm": null,
            "max_focal_length_mm": null,
            "min_aperture_fstop": 1.0,
            "max_aperture_fstop": 32.0,
            "minimum_focus_distance_cm": 0.0,
            "camera": {
                "location_cm": {"x": 10.0, "y": 20.0, "z": 30.0},
                "rotation_deg": {"x": 0.0, "y": 0.0, "z": 0.0},
                "field_of_view_deg": 60.0,
                "focal_length_mm": null,
                "aperture_fstop": 4.0,
                "focus_mode": "project_default",
                "focus_distance_cm": 1000.0,
                "exposure_compensation_enabled": false,
                "exposure_compensation_ev": 0.0,
                "post_process_blend_weight": 1.0
            }
        },
        "proposed_by": {"provider": "ollama", "model": "gpt-oss:20b"},
        "before": {
            "location_cm": {"x": 10.0, "y": 20.0, "z": 30.0},
            "rotation_deg": {"x": 0.0, "y": 0.0, "z": 0.0},
            "field_of_view_deg": 60.0,
            "focal_length_mm": null,
            "aperture_fstop": 4.0,
            "focus_mode": "project_default",
            "focus_distance_cm": 1000.0,
            "exposure_compensation_enabled": false,
            "exposure_compensation_ev": 0.0,
            "post_process_blend_weight": 1.0
        },
        "after": {
            "location_cm": {"x": 10.0, "y": 20.0, "z": 130.0},
            "rotation_deg": {"x": 0.0, "y": 0.0, "z": 0.0},
            "field_of_view_deg": 75.0,
            "focal_length_mm": null,
            "aperture_fstop": 5.6,
            "focus_mode": "manual",
            "focus_distance_cm": 350.0,
            "exposure_compensation_enabled": true,
            "exposure_compensation_ev": 1.0,
            "post_process_blend_weight": 1.0
        },
        "changes": [
            {"property": "location", "operation": "add"},
            {"property": "field_of_view_deg", "operation": "set"},
            {"property": "aperture_fstop", "operation": "set"},
            {"property": "focus_mode", "operation": "set"},
            {"property": "focus_distance_cm", "operation": "set"},
            {"property": "exposure_compensation_enabled", "operation": "set"},
            {"property": "exposure_compensation_ev", "operation": "add"}
        ],
        "rationale": "Apply only bounded, user-requested camera properties.",
        "missing_capabilities": [],
        "modifies_editor_scene": true,
        "auto_save": false,
        "undo_supported": true
    })JSON");

    const FString ProposalPath = FPaths::CreateTempFilename(
        FPlatformProcess::UserTempDir(), TEXT("RenderMasterCameraProposal"), TEXT(".json"));
    if (!FFileHelper::SaveStringToFile(ProposalJson, *ProposalPath))
    {
        AddError(TEXT("Could not write the temporary camera proposal."));
        return false;
    }

    FRenderMasterCameraProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Camera proposal parses"),
        RenderMasterParseCameraProposalFile(ProposalPath, Proposal, Error));
    TestEqual(TEXT("Camera kind"), Proposal.CameraKind, FString(TEXT("camera")));
    TestTrue(TEXT("After FOV is preserved"), Proposal.After.FieldOfViewDeg.IsSet()
        && FMath::IsNearlyEqual(Proposal.After.FieldOfViewDeg.GetValue(), 75.0));
    TestTrue(TEXT("Change summary names focus"), Proposal.ChangeSummary.Contains(TEXT("focus_mode")));

    const FString TamperedJson = ProposalJson.Replace(
        TEXT("{\"property\": \"field_of_view_deg\", \"operation\": \"set\"}"),
        TEXT("{\"property\": \"focal_length_mm\", \"operation\": \"set\"}"),
        ESearchCase::CaseSensitive);
    TestTrue(TEXT("Tampered proposal fixture writes"), FFileHelper::SaveStringToFile(TamperedJson, *ProposalPath));
    TestFalse(
        TEXT("Camera change list must match type-specific Before and After evidence"),
        RenderMasterParseCameraProposalFile(ProposalPath, Proposal, Error));
    IFileManager::Get().Delete(*ProposalPath, false, true);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterCameraBatchProposalParseTest,
    "RenderMasterBot.Editor.Assistant.CameraBatchProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterCameraBatchProposalParseTest::RunTest(const FString& Parameters)
{
    const FString CameraA = TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
        "actor_name":"CameraA","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.CameraA",
        "actor_class":"CameraActor","actor_guid":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "component_name":"CameraComponent","camera_kind":"camera","component_mobility":"movable",
        "projection_mode":"perspective","is_editable":true,"is_locked":false,
        "min_focal_length_mm":null,"max_focal_length_mm":null,
        "min_aperture_fstop":1.0,"max_aperture_fstop":32.0,"minimum_focus_distance_cm":0.0,
        "camera":{"location_cm":{"x":0,"y":-300,"z":150},"rotation_deg":{"x":0,"y":-10,"z":0},
        "field_of_view_deg":90,"focal_length_mm":null,"aperture_fstop":4,
        "focus_mode":"project_default","focus_distance_cm":1000,
        "exposure_compensation_enabled":true,"exposure_compensation_ev":1,
        "post_process_blend_weight":1}
    })JSON");
    const FString CameraB = TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap",
        "actor_name":"CameraB","actor_path":"/Game/Maps/TestMap.TestMap:PersistentLevel.CameraB",
        "actor_class":"CameraActor","actor_guid":"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "component_name":"CameraComponent","camera_kind":"camera","component_mobility":"movable",
        "projection_mode":"perspective","is_editable":true,"is_locked":false,
        "min_focal_length_mm":null,"max_focal_length_mm":null,
        "min_aperture_fstop":1.0,"max_aperture_fstop":32.0,"minimum_focus_distance_cm":0.0,
        "camera":{"location_cm":{"x":200,"y":-300,"z":150},"rotation_deg":{"x":0,"y":-10,"z":15},
        "field_of_view_deg":90,"focal_length_mm":null,"aperture_fstop":4,
        "focus_mode":"project_default","focus_distance_cm":1000,
        "exposure_compensation_enabled":false,"exposure_compensation_ev":0,
        "post_process_blend_weight":1}
    })JSON");
    const FString ProposalJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","proposal_id":"camera_batch_001","status":"proposed",
        "request":"Match all selected cameras to plus one EV.",
        "selection":{"schema_version":"0.1","project_name":"OptimizationPlugin","level_path":"/Game/Maps/TestMap","cameras":[%s,%s]},
        "proposed_by":{"provider":"ollama","model":"gpt-oss:20b"},
        "actions":[
            {"target":%s,
             "before":{"location_cm":{"x":0,"y":-300,"z":150},"rotation_deg":{"x":0,"y":-10,"z":0},"field_of_view_deg":90,"focal_length_mm":null,"aperture_fstop":4,"focus_mode":"project_default","focus_distance_cm":1000,"exposure_compensation_enabled":true,"exposure_compensation_ev":1,"post_process_blend_weight":1},
             "after":{"location_cm":{"x":0,"y":-300,"z":150},"rotation_deg":{"x":0,"y":-10,"z":0},"field_of_view_deg":90,"focal_length_mm":null,"aperture_fstop":4,"focus_mode":"project_default","focus_distance_cm":1000,"exposure_compensation_enabled":true,"exposure_compensation_ev":1,"post_process_blend_weight":1},
             "changes":[]},
            {"target":%s,
             "before":{"location_cm":{"x":200,"y":-300,"z":150},"rotation_deg":{"x":0,"y":-10,"z":15},"field_of_view_deg":90,"focal_length_mm":null,"aperture_fstop":4,"focus_mode":"project_default","focus_distance_cm":1000,"exposure_compensation_enabled":false,"exposure_compensation_ev":0,"post_process_blend_weight":1},
             "after":{"location_cm":{"x":200,"y":-300,"z":150},"rotation_deg":{"x":0,"y":-10,"z":15},"field_of_view_deg":90,"focal_length_mm":null,"aperture_fstop":4,"focus_mode":"project_default","focus_distance_cm":1000,"exposure_compensation_enabled":true,"exposure_compensation_ev":1,"post_process_blend_weight":1},
             "changes":[{"property":"exposure_compensation_enabled","operation":"set"},{"property":"exposure_compensation_ev","operation":"set"}]}
        ],
        "rationale":"Use one shared exposure target while preserving every shot Transform.",
        "missing_capabilities":[],"modifies_editor_scene":true,"auto_save":false,"undo_supported":true
    })JSON"), *CameraA, *CameraB, *CameraA, *CameraB);

    const FString ProposalPath = FPaths::CreateTempFilename(
        FPlatformProcess::UserTempDir(), TEXT("RenderMasterCameraBatchProposal"), TEXT(".json"));
    TestTrue(TEXT("Camera batch proposal fixture writes"),
        FFileHelper::SaveStringToFile(ProposalJson, *ProposalPath));

    FRenderMasterCameraBatchProposal Proposal;
    FString Error;
    TestTrue(TEXT("Complete camera batch proposal parses"),
        RenderMasterParseCameraBatchProposalFile(ProposalPath, Proposal, Error));
    TestEqual(TEXT("Batch preserves two ordered cameras"), Proposal.Actions.Num(), 2);
    TestTrue(TEXT("Already-matched camera remains an explicit no-op"),
        Proposal.Actions[0].ChangeSummary.Contains(TEXT("already at target")));
    TestTrue(TEXT("Second camera receives exposure changes"),
        Proposal.Actions[1].ChangeSummary.Contains(TEXT("exposure_compensation_ev")));

    FString TamperedJson = ProposalJson;
    const int32 ActionsStart = TamperedJson.Find(TEXT("\"actions\""));
    const FString CameraBName = TEXT("\"actor_name\":\"CameraB\"");
    const int32 ActionCameraB = TamperedJson.Find(
        CameraBName, ESearchCase::CaseSensitive, ESearchDir::FromStart, ActionsStart);
    if (ActionCameraB != INDEX_NONE)
    {
        TamperedJson.RemoveAt(ActionCameraB, CameraBName.Len(), EAllowShrinking::No);
        TamperedJson.InsertAt(ActionCameraB, TEXT("\"actor_name\":\"SpoofedCamera\""));
    }
    TestTrue(TEXT("Tampered camera batch fixture writes"),
        FFileHelper::SaveStringToFile(TamperedJson, *ProposalPath));
    TestFalse(TEXT("Action target must match the frozen ordered camera selection"),
        RenderMasterParseCameraBatchProposalFile(ProposalPath, Proposal, Error));

    IFileManager::Get().Delete(*ProposalPath, false, true, true);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterCameraBatchApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.CameraBatchApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterCameraBatchApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    TArray<ACameraActor*> Actors;
    TArray<UCameraComponent*> Components;
    TArray<FRenderMasterCameraProposal> Actions;
    for (int32 Index = 0; Index < 2; ++Index)
    {
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = MakeUniqueObjectName(
            World->PersistentLevel,
            ACameraActor::StaticClass(),
            *FString::Printf(TEXT("RenderMasterCameraBatchUndoTest%d"), Index));
        SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
        SpawnParameters.SpawnCollisionHandlingOverride =
            ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        ACameraActor* Actor = World->SpawnActor<ACameraActor>(
            ACameraActor::StaticClass(),
            FVector(Index * 200.0, -300.0, 150.0),
            FRotator(-5.0, Index * 15.0, 0.0),
            SpawnParameters);
        if (Actor == nullptr || Actor->GetCameraComponent() == nullptr)
        {
            AddError(TEXT("Could not spawn a transient camera batch test Actor."));
            for (ACameraActor* Spawned : Actors)
                World->DestroyActor(Spawned, false, false);
            return false;
        }
        UCameraComponent* Component = Actor->GetCameraComponent();
        Component->PostProcessBlendWeight = 1.0f;
        Component->PostProcessSettings.bOverride_AutoExposureBias = false;
        Component->PostProcessSettings.AutoExposureBias = 0.0f;

        FRenderMasterCameraProposal Action;
        Action.CameraKind = RenderMasterCameraKind(Component);
        Action.Bounds = RenderMasterSnapshotCameraBounds(Component);
        Action.Before = RenderMasterSnapshotCamera(Actor, Component);
        Action.After = Action.Before;
        Action.After.Location.Z += 50.0;
        Action.After.bExposureCompensationEnabled = true;
        Action.After.ExposureCompensationEv = 1.0;
        Actors.Add(Actor);
        Components.Add(Component);
        Actions.Add(Action);
    }

    FString Error;
    TestTrue(
        TEXT("Grouped camera helper applies"),
        RenderMasterApplyCameraPropertiesBatch(
            Actors, Components, Actions, Error, false));
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        TestTrue(
            *FString::Printf(TEXT("Camera %d receives its proposed location"), Index),
            Actors[Index]->GetActorLocation().Equals(Actions[Index].After.Location, 0.01));
        TestTrue(
            *FString::Printf(TEXT("Camera %d enables exposure override"), Index),
            Components[Index]->PostProcessSettings.bOverride_AutoExposureBias);
        TestTrue(
            *FString::Printf(TEXT("Camera %d receives plus one EV"), Index),
            FMath::IsNearlyEqual(
                Components[Index]->PostProcessSettings.AutoExposureBias, 1.0f));
    }

    TestTrue(
        TEXT("One Unreal transaction undoes the complete camera batch"),
        GEditor->UndoTransaction(false));
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        TestTrue(
            *FString::Printf(TEXT("Undo restores camera %d location"), Index),
            Actors[Index]->GetActorLocation().Equals(Actions[Index].Before.Location, 0.01));
        TestFalse(
            *FString::Printf(TEXT("Undo restores camera %d exposure override"), Index),
            Components[Index]->PostProcessSettings.bOverride_AutoExposureBias);
        World->DestroyActor(Actors[Index], false, false);
    }
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterCameraApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.CameraApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterCameraApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    if (World == nullptr)
    {
        AddError(TEXT("Editor world is unavailable."));
        return false;
    }

    FActorSpawnParameters SpawnParameters;
    SpawnParameters.Name = MakeUniqueObjectName(
        World->PersistentLevel,
        ACameraActor::StaticClass(),
        TEXT("RenderMasterCameraUndoTest"));
    SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
    SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    ACameraActor* Actor = World->SpawnActor<ACameraActor>(
        ACameraActor::StaticClass(),
        FVector::ZeroVector,
        FRotator::ZeroRotator,
        SpawnParameters);
    if (Actor == nullptr)
    {
        AddError(TEXT("Could not spawn the transient Camera test Actor."));
        return false;
    }
    UCameraComponent* Component = Actor->GetCameraComponent();
    if (Component == nullptr)
    {
        AddError(TEXT("The transient Camera Actor has no Camera Component."));
        World->DestroyActor(Actor, false, false);
        return false;
    }
    Component->SetFlags(RF_Transactional);

    const FVector OriginalLocation(10.0, 20.0, 30.0);
    Actor->SetActorLocation(OriginalLocation, false, nullptr, ETeleportType::TeleportPhysics);
    Component->FieldOfView = 60.0f;
    Component->PostProcessBlendWeight = 1.0f;
    Component->PostProcessSettings.DepthOfFieldFstop = 4.0f;
    Component->PostProcessSettings.DepthOfFieldFocalDistance = 1000.0f;
    Component->PostProcessSettings.bOverride_DepthOfFieldFstop = false;
    Component->PostProcessSettings.bOverride_DepthOfFieldFocalDistance = false;
    Component->PostProcessSettings.bOverride_AutoExposureBias = false;
    Component->PostProcessSettings.AutoExposureBias = 0.0f;

    FRenderMasterCameraBounds Bounds;
    Bounds.MinApertureFstop = 1.0;
    Bounds.MaxApertureFstop = 32.0;
    Bounds.MinimumFocusDistanceCm = 0.0;
    FRenderMasterCameraSnapshot Before;
    Before.Location = OriginalLocation;
    Before.Rotation = FRotator::ZeroRotator;
    Before.FieldOfViewDeg = 60.0;
    Before.ApertureFstop = 4.0;
    Before.FocusMode = TEXT("project_default");
    Before.FocusDistanceCm = 1000.0;
    Before.bExposureCompensationEnabled = false;
    Before.ExposureCompensationEv = 0.0;
    Before.PostProcessBlendWeight = 1.0;

    FRenderMasterCameraSnapshot After = Before;
    After.Location = FVector(10.0, 20.0, 130.0);
    After.Rotation = FRotator(-10.0, 25.0, 0.0);
    After.FieldOfViewDeg = 75.0;
    After.ApertureFstop = 5.6;
    After.FocusMode = TEXT("manual");
    After.FocusDistanceCm = 350.0;
    After.bExposureCompensationEnabled = true;
    After.ExposureCompensationEv = 1.0;

    FString Error;
    const bool bApplied = RenderMasterApplyCameraProperties(
        Actor, Component, TEXT("camera"), Bounds, Before, After, Error, false);
    TestTrue(TEXT("Approved camera helper applies"), bApplied);
    TestTrue(TEXT("Applied camera location matches"), Actor->GetActorLocation().Equals(After.Location, 0.01));
    TestTrue(TEXT("Applied camera rotation matches"), Actor->GetActorRotation().Equals(After.Rotation, 0.01));
    TestTrue(TEXT("Applied FOV matches"), FMath::IsNearlyEqual(Component->FieldOfView, 75.0f));
    TestTrue(TEXT("Applied aperture matches"), FMath::IsNearlyEqual(Component->PostProcessSettings.DepthOfFieldFstop, 5.6f));
    TestTrue(TEXT("Manual focus overrides are enabled"), Component->PostProcessSettings.bOverride_DepthOfFieldFstop
        && Component->PostProcessSettings.bOverride_DepthOfFieldFocalDistance);
    TestTrue(TEXT("Applied focus distance matches"), FMath::IsNearlyEqual(Component->PostProcessSettings.DepthOfFieldFocalDistance, 350.0f));
    TestTrue(TEXT("Applied exposure compensation matches"), Component->PostProcessSettings.bOverride_AutoExposureBias
        && FMath::IsNearlyEqual(Component->PostProcessSettings.AutoExposureBias, 1.0f));

    const bool bUndone = GEditor->UndoTransaction(false);
    TestTrue(TEXT("Camera transaction can be undone"), bUndone);
    TestTrue(TEXT("Undo restores camera location"), Actor->GetActorLocation().Equals(OriginalLocation, 0.01));
    TestTrue(TEXT("Undo restores FOV"), FMath::IsNearlyEqual(Component->FieldOfView, 60.0f));
    TestFalse(TEXT("Undo restores focus override state"), Component->PostProcessSettings.bOverride_DepthOfFieldFstop);
    TestFalse(TEXT("Undo restores exposure override state"), Component->PostProcessSettings.bOverride_AutoExposureBias);
    World->DestroyActor(Actor, false, false);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterPerformanceProposalTest,
    "RenderMasterBot.Editor.Assistant.PerformanceProposal",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterPerformanceProposalTest::RunTest(const FString& Parameters)
{
    const FString ActorPath =
        TEXT("/Game/Maps/TestMap:PersistentLevel.PerformanceMeshA");
    const FString ActorJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","project_name":"OptimizationPlugin",
        "level_path":"/Game/Maps/TestMap","actor_name":"PerformanceMeshA",
        "actor_path":"%s","actor_class":"StaticMeshActor",
        "actor_guid":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "component_name":"StaticMeshComponent0","component_mobility":"static",
        "is_editable":true,"is_locked":false,
        "mesh_path":"/Engine/BasicShapes/Cube.Cube","lod_count":1,
        "lod0_triangles":12,"material_slot_count":1,"nanite_enabled":false,
        "collision_mode":"query_and_physics","component_tick_enabled":false,
        "bounds_radius_cm":86.6,
        "performance":{"cast_shadow":true,"max_draw_distance_cm":0.0}
    })JSON"), *ActorPath);
    const FString ProposalJson = FString::Printf(TEXT(R"JSON({
        "schema_version":"0.1","proposal_id":"performance_test",
        "status":"proposed","request":"Optimize the selected background mesh",
        "selection":{"schema_version":"0.1","project_name":"OptimizationPlugin",
            "level_path":"/Game/Maps/TestMap","actors":[%s]},
        "proposed_by":{"provider":"ollama","model":"test-model"},
        "summary":"Apply only the explicit component-level settings.",
        "findings":[{"actor_path":"%s","severity":"warning",
            "category":"culling","evidence_fields":["max_draw_distance_cm","bounds_radius_cm"],
            "recommendation":"Use the user-supplied cull distance for this background mesh."}],
        "actions":[{"target":%s,
            "before":{"cast_shadow":true,"max_draw_distance_cm":0.0},
            "after":{"cast_shadow":false,"max_draw_distance_cm":10000.0},
            "changes":[
                {"property":"cast_shadow","before":true,"after":false},
                {"property":"max_draw_distance_cm","before":0.0,"after":10000.0}],
            "rationale":"The user explicitly requested both bounded settings."}],
        "missing_capabilities":[],"modifies_editor_scene":true,
        "auto_save":false,"undo_supported":true
    })JSON"), *ActorJson, *ActorPath, *ActorJson);
    const FString ProposalPath = FPaths::CreateTempFilename(
        *FPaths::ProjectIntermediateDir(), TEXT("RenderMasterPerformance"), TEXT(".json"));
    TestTrue(
        TEXT("Performance proposal fixture writes"),
        FFileHelper::SaveStringToFile(ProposalJson, *ProposalPath));

    FRenderMasterPerformanceProposal Proposal;
    FString Error;
    TestTrue(
        TEXT("Evidence-bound performance proposal parses"),
        RenderMasterParsePerformanceProposalFile(ProposalPath, Proposal, Error));
    TestEqual(TEXT("One complete performance action"), Proposal.Actions.Num(), 1);
    if (Proposal.Actions.Num() == 1)
    {
        TestFalse(TEXT("After disables shadow"), Proposal.Actions[0].After.bCastShadow);
        TestTrue(
            TEXT("After sets exact cull distance"),
            FMath::IsNearlyEqual(
                Proposal.Actions[0].After.MaxDrawDistanceCm, 10000.0f));
    }

    FString TamperedJson = ProposalJson;
    const int32 ActionsOffset = TamperedJson.Find(
        TEXT("\"actions\":[{\"target\":"),
        ESearchCase::CaseSensitive);
    const int32 TargetPathOffset = TamperedJson.Find(
        *ActorPath,
        ESearchCase::CaseSensitive,
        ESearchDir::FromStart,
        ActionsOffset);
    TestTrue(TEXT("Action target path can be located"), TargetPathOffset != INDEX_NONE);
    if (TargetPathOffset != INDEX_NONE)
    {
        TamperedJson.RemoveAt(
            TargetPathOffset, ActorPath.Len(), EAllowShrinking::No);
        TamperedJson.InsertAt(
            TargetPathOffset,
            TEXT("/Game/Maps/TestMap:PersistentLevel.TamperedMesh"));
    }
    TestTrue(
        TEXT("Tampered performance fixture writes"),
        FFileHelper::SaveStringToFile(TamperedJson, *ProposalPath));
    FRenderMasterPerformanceProposal Tampered;
    FString TamperedError;
    TestFalse(
        TEXT("Tampered ordered target is rejected"),
        RenderMasterParsePerformanceProposalFile(
            ProposalPath, Tampered, TamperedError));
    IFileManager::Get().Delete(*ProposalPath, false, true, true);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterPerformanceApplyUndoTest,
    "RenderMasterBot.Editor.Assistant.PerformanceApplyUndo",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterPerformanceApplyUndoTest::RunTest(const FString& Parameters)
{
    if (GEditor == nullptr)
    {
        AddError(TEXT("Editor service is unavailable."));
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext(false).World();
    UStaticMesh* Cube = LoadObject<UStaticMesh>(
        nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    if (World == nullptr || Cube == nullptr)
    {
        AddError(TEXT("Editor world or Engine cube asset is unavailable."));
        return false;
    }

    TArray<AStaticMeshActor*> Actors;
    TArray<UStaticMeshComponent*> Components;
    TArray<FRenderMasterPerformanceAction> Actions;
    for (int32 Index = 0; Index < 2; ++Index)
    {
        FActorSpawnParameters SpawnParameters;
        SpawnParameters.Name = MakeUniqueObjectName(
            World->PersistentLevel,
            AStaticMeshActor::StaticClass(),
            *FString::Printf(TEXT("RenderMasterPerformanceUndo%d"), Index));
        SpawnParameters.ObjectFlags = RF_Transient | RF_Transactional;
        SpawnParameters.SpawnCollisionHandlingOverride =
            ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(
            AStaticMeshActor::StaticClass(),
            FVector(Index * 200.0, 0.0, 0.0),
            FRotator::ZeroRotator,
            SpawnParameters);
        if (Actor == nullptr)
        {
            AddError(TEXT("Could not spawn a transient StaticMeshActor."));
            return false;
        }
        UStaticMeshComponent* Component = Actor->GetStaticMeshComponent();
        Component->SetFlags(RF_Transactional);
        Component->SetStaticMesh(Cube);
        Component->SetCastShadow(true);
        Component->SetCullDistance(0.0f);
        Component->UpdateBounds();

        FRenderMasterPerformanceEvidence Evidence;
        FString CaptureError;
        if (!RenderMasterCapturePerformanceEvidence(
                Actor, Component, Evidence, CaptureError))
        {
            AddError(CaptureError);
            World->DestroyActor(Actor, false, false);
            return false;
        }
        FRenderMasterPerformanceAction Action;
        Action.Target = Evidence;
        Action.Before = Evidence.Before;
        Action.After = Action.Before;
        Action.After.bCastShadow = false;
        Action.After.MaxDrawDistanceCm = 10000.0f + Index * 1000.0f;
        Action.Rationale = TEXT("Automation test bounded performance action.");
        Actors.Add(Actor);
        Components.Add(Component);
        Actions.Add(Action);
    }

    FString Error;
    TestTrue(
        TEXT("Grouped performance helper applies"),
        RenderMasterApplyPerformanceBatch(
            Actors, Components, Actions, Error, false));
    for (int32 Index = 0; Index < Components.Num(); ++Index)
    {
        TestFalse(
            *FString::Printf(TEXT("Actor %d shadow disabled"), Index),
            Components[Index]->CastShadow);
        TestTrue(
            *FString::Printf(TEXT("Actor %d receives cull distance"), Index),
            FMath::IsNearlyEqual(
                Components[Index]->LDMaxDrawDistance,
                Actions[Index].After.MaxDrawDistanceCm));
    }

    TestTrue(
        TEXT("One Unreal transaction undoes the complete performance batch"),
        GEditor->UndoTransaction(false));
    for (int32 Index = 0; Index < Components.Num(); ++Index)
    {
        TestTrue(
            *FString::Printf(TEXT("Undo restores Actor %d shadow"), Index),
            Components[Index]->CastShadow);
        TestTrue(
            *FString::Printf(TEXT("Undo restores Actor %d cull distance"), Index),
            FMath::IsNearlyEqual(Components[Index]->LDMaxDrawDistance, 0.0f));
        World->DestroyActor(Actors[Index], false, false);
    }
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterRuntimeTimingSummaryTest,
    "RenderMasterBot.Editor.Assistant.RuntimePerformanceSummary",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterRuntimeTimingSummaryTest::RunTest(const FString& Parameters)
{
    FRenderMasterRuntimeTimingSummary Summary;
    TestTrue(
        TEXT("Finite timing samples summarize"),
        RenderMasterSummarizeRuntimeTimings(
            {16.0, 16.0, 16.0, 22.0, 30.0}, Summary));
    TestTrue(TEXT("Summary is available"), Summary.bAvailable);
    TestEqual(TEXT("Summary sample count"), Summary.SampleCount, 5);
    TestTrue(TEXT("Nearest-rank P50"), FMath::IsNearlyEqual(Summary.P50Ms, 16.0));
    TestTrue(TEXT("Nearest-rank P95"), FMath::IsNearlyEqual(Summary.P95Ms, 30.0));
    TestTrue(TEXT("Maximum timing"), FMath::IsNearlyEqual(Summary.MaxMs, 30.0));
    TestFalse(
        TEXT("Negative timing is rejected"),
        RenderMasterSummarizeRuntimeTimings({16.0, -1.0}, Summary));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterRuntimePerformanceReportTest,
    "RenderMasterBot.Editor.Assistant.RuntimePerformanceReport",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterRuntimePerformanceReportTest::RunTest(const FString& Parameters)
{
    const FString Json = TEXT(R"JSON({
        "schema_version": "0.1",
        "report_id": "runtime_capture_001_review",
        "status": "review_complete",
        "request": "Diagnose this PIE capture against 60 FPS.",
        "capture": {
            "schema_version": "0.1",
            "capture_id": "runtime_capture_001"
        },
        "capture_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "analyzed_by": {"provider": "ollama", "model": "gpt-oss:20b"},
        "summary": "Frame p95 exceeds the target while measured GPU p95 remains lower.",
        "primary_bottleneck": "inconclusive",
        "findings": [{
            "severity": "warning",
            "category": "frame_pacing",
            "evidence_fields": ["target_frame_ms", "frame_time.p95_ms"],
            "observation": "Frame p95 is above the supplied budget.",
            "recommendation": "Capture a longer representative workload."
        }],
        "missing_capabilities": [],
        "modifies_editor_scene": false
    })JSON");
    const FString Path = FPaths::Combine(
        FPaths::ProjectIntermediateDir(),
        TEXT("RenderMasterRuntimePerformanceReportTest.json"));
    TestTrue(
        TEXT("Runtime report fixture writes"),
        FFileHelper::SaveStringToFile(Json, *Path));
    FRenderMasterRuntimePerformanceReport Report;
    FString Error;
    TestTrue(
        TEXT("Read-only runtime report parses"),
        RenderMasterParseRuntimePerformanceReportFile(Path, Report, Error));
    TestEqual(
        TEXT("Capture identity is preserved"),
        Report.CaptureId,
        FString(TEXT("runtime_capture_001")));
    TestEqual(TEXT("One finding is preserved"), Report.Findings.Num(), 1);
    TestFalse(TEXT("Report cannot modify the Editor"), Report.bModifiesEditorScene);

    FString Tampered = Json.Replace(
        TEXT("\"modifies_editor_scene\": false"),
        TEXT("\"modifies_editor_scene\": true"));
    TestTrue(
        TEXT("Tampered report fixture writes"),
        FFileHelper::SaveStringToFile(Tampered, *Path));
    TestFalse(
        TEXT("Scene-modifying runtime report is rejected"),
        RenderMasterParseRuntimePerformanceReportFile(Path, Report, Error));
    IFileManager::Get().Delete(*Path, false, true);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterInsightsGpuAggregationTest,
    "RenderMasterBot.Editor.Assistant.InsightsGpuAggregation",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterInsightsGpuAggregationTest::RunTest(const FString& Parameters)
{
    TArray<FRenderMasterInsightsScopeSample> Samples;
    Samples.Add({TEXT("queue_000"), TEXT("GPU0-Graphics0"), TEXT("BasePass"), 2.0, 1});
    Samples.Add({TEXT("queue_000"), TEXT("GPU0-Graphics0"), TEXT("BasePass"), 4.0, 2});
    Samples.Add({TEXT("queue_000"), TEXT("GPU0-Graphics0"), TEXT("Lumen"), 8.0, 1});
    Samples.Add({TEXT("queue_001"), TEXT("GPU0-Compute0"), TEXT("AsyncWork"), 1.0, 0});
    TArray<FRenderMasterInsightsScopeAggregate> Scopes;
    FString Error;
    TestTrue(
        TEXT("Finite GPU scope samples aggregate"),
        RenderMasterAggregateInsightsGpuScopes(Samples, 64, Scopes, Error));
    TestEqual(TEXT("Three queue-local scopes remain"), Scopes.Num(), 3);
    TestEqual(TEXT("Largest accumulated scope sorts first"), Scopes[0].Name, FString(TEXT("Lumen")));
    TestEqual(TEXT("Stable scope ID assigned"), Scopes[0].ScopeId, FString(TEXT("scope_000")));
    TestEqual(TEXT("Repeated BasePass count"), Scopes[1].InstanceCount, 2);
    TestTrue(TEXT("Repeated BasePass total"), FMath::IsNearlyEqual(Scopes[1].TotalInclusiveMs, 6.0));
    TestTrue(TEXT("Repeated BasePass mean"), FMath::IsNearlyEqual(Scopes[1].MeanInclusiveMs, 3.0));
    TestEqual(TEXT("Minimum depth retained"), Scopes[1].MinDepth, static_cast<uint32>(1));
    TestEqual(TEXT("Maximum depth retained"), Scopes[1].MaxDepth, static_cast<uint32>(2));

    Samples[0].InclusiveMs = -1.0;
    TestFalse(
        TEXT("Negative GPU timing is rejected"),
        RenderMasterAggregateInsightsGpuScopes(Samples, 64, Scopes, Error));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterInsightsGpuReportTest,
    "RenderMasterBot.Editor.Assistant.InsightsGpuReport",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterInsightsGpuReportTest::RunTest(const FString& Parameters)
{
    const FString Json = TEXT(R"JSON({
        "schema_version": "0.1",
        "report_id": "insights_capture_001_review",
        "status": "review_complete",
        "request": "Rank measured GPU scopes.",
        "capture": {
            "schema_version": "0.1",
            "capture_id": "insights_capture_001"
        },
        "capture_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "capture_file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "analyzed_by": {"provider": "ollama", "model": "gpt-oss:20b"},
        "summary": "Lumen is the highest accumulated inclusive scope in this trace.",
        "primary_scope_id": "scope_000",
        "findings": [{
            "severity": "warning",
            "category": "gpu_scope",
            "evidence_scope_ids": ["scope_000"],
            "observation": "The measured scope ranks first by accumulated inclusive time.",
            "recommendation": "Repeat the same trace with one bounded quality experiment."
        }],
        "missing_capabilities": [],
        "modifies_editor_scene": false
    })JSON");
    const FString Path = FPaths::Combine(
        FPaths::ProjectIntermediateDir(),
        TEXT("RenderMasterInsightsGpuReportTest.json"));
    TestTrue(TEXT("GPU report fixture writes"), FFileHelper::SaveStringToFile(Json, *Path));
    FRenderMasterInsightsGpuReport Report;
    FString Error;
    TestTrue(
        TEXT("Read-only GPU scope report parses"),
        RenderMasterParseInsightsGpuReportFile(Path, Report, Error));
    TestEqual(
        TEXT("GPU capture identity is preserved"),
        Report.CaptureId,
        FString(TEXT("insights_capture_001")));
    TestEqual(TEXT("Primary scope is preserved"), Report.PrimaryScopeId, FString(TEXT("scope_000")));
    TestEqual(
        TEXT("Capture artifact hash is preserved"),
        Report.CaptureFileSha256,
        FString(TEXT("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")));
    TestEqual(TEXT("One GPU finding is preserved"), Report.Findings.Num(), 1);
    TestEqual(TEXT("One cited GPU scope is preserved"), Report.CitedScopeIds.Num(), 1);
    if (Report.CitedScopeIds.Num() == 1)
    {
        TestEqual(
            TEXT("Cited GPU scope identity is preserved"),
            Report.CitedScopeIds[0],
            FString(TEXT("scope_000")));
    }
    TestFalse(TEXT("GPU report cannot modify the Editor"), Report.bModifiesEditorScene);

    FString Tampered = Json.Replace(
        TEXT("\"modifies_editor_scene\": false"),
        TEXT("\"modifies_editor_scene\": true"));
    TestTrue(TEXT("Tampered GPU report fixture writes"), FFileHelper::SaveStringToFile(Tampered, *Path));
    TestFalse(
        TEXT("Scene-modifying GPU report is rejected"),
        RenderMasterParseInsightsGpuReportFile(Path, Report, Error));
    IFileManager::Get().Delete(*Path, false, true);
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterActorGpuImpactComparisonTest,
    "RenderMasterBot.Editor.Assistant.ActorGpuImpactComparison",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterActorGpuImpactComparisonTest::RunTest(const FString& Parameters)
{
    auto MakeCapture = [](const FString& Id)
    {
        FRenderMasterInsightsGpuCapture Value;
        Value.CaptureId = Id;
        Value.ProjectName = TEXT("OptimizationPlugin");
        Value.WorldPath = TEXT("/Game/Maps/Test");
        Value.CaptureMode = TEXT("pie");
        Value.CapturedDurationSeconds = 5.0;
        Value.ViewportSize = FIntPoint(1920, 1080);
        Value.GpuName = TEXT("Test GPU");
        Value.Channels = {TEXT("cpu"), TEXT("gpu"), TEXT("frame"), TEXT("bookmark")};
        FRenderMasterInsightsGpuQueue Queue;
        Queue.QueueId = TEXT("queue_000");
        Queue.DisplayName = TEXT("GPU0-Graphics0");
        Value.Queues.Add(Queue);
        return Value;
    };
    FRenderMasterInsightsGpuCapture Baseline = MakeCapture(TEXT("baseline"));
    FRenderMasterInsightsGpuCapture Variant = MakeCapture(TEXT("variant"));
    FRenderMasterInsightsScopeAggregate BasePass;
    BasePass.ScopeId = TEXT("scope_000");
    BasePass.QueueId = TEXT("queue_000");
    BasePass.QueueName = TEXT("GPU0-Graphics0");
    BasePass.Name = TEXT("BasePass");
    BasePass.InstanceCount = 20;
    BasePass.TotalInclusiveMs = 30.0;
    Baseline.Scopes.Add(BasePass);
    FRenderMasterInsightsScopeAggregate Shadows = BasePass;
    Shadows.ScopeId = TEXT("scope_001");
    Shadows.Name = TEXT("ShadowDepths");
    Shadows.TotalInclusiveMs = 40.0;
    Baseline.Scopes.Add(Shadows);
    BasePass.TotalInclusiveMs = 25.0;
    Variant.Scopes.Add(BasePass);
    Shadows.TotalInclusiveMs = 20.0;
    Variant.Scopes.Add(Shadows);
    TArray<FRenderMasterActorGpuScopeDelta> Deltas;
    TArray<FString> UnmatchedBaseline;
    TArray<FString> UnmatchedVariant;
    FString Error;
    TestTrue(
        TEXT("Matched Actor GPU scopes compare"),
        RenderMasterCompareActorGpuCaptures(
            Baseline,
            Variant,
            Deltas,
            UnmatchedBaseline,
            UnmatchedVariant,
            Error));
    TestEqual(TEXT("Two matched deltas are retained"), Deltas.Num(), 2);
    if (Deltas.Num() == 2)
    {
        TestEqual(TEXT("Largest delta ranks first"), Deltas[0].ScopeName, FString(TEXT("ShadowDepths")));
        TestEqual(TEXT("Stable delta ID is assigned"), Deltas[0].DeltaId, FString(TEXT("delta_000")));
        TestTrue(
            TEXT("Shadow delta is normalized and recomputable"),
            FMath::IsNearlyEqual(
                Deltas[0].BaselineMinusVariantMsPerSecond,
                4.0,
                0.001));
        TestEqual(TEXT("Hidden direction is explicit"), Deltas[0].DirectionWhenHidden, FString(TEXT("decreased")));
    }
    TestTrue(TEXT("All baseline scopes matched"), UnmatchedBaseline.IsEmpty());
    TestTrue(TEXT("All variant scopes matched"), UnmatchedVariant.IsEmpty());
    Variant.ViewportSize.X = 1280;
    TestFalse(
        TEXT("Environment drift is rejected"),
        RenderMasterCompareActorGpuCaptures(
            Baseline,
            Variant,
            Deltas,
            UnmatchedBaseline,
            UnmatchedVariant,
            Error));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterActorGpuImpactReportTest,
    "RenderMasterBot.Editor.Assistant.ActorGpuImpactReport",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterActorGpuImpactReportTest::RunTest(const FString& Parameters)
{
    const FString Json = TEXT(R"JSON({
        "schema_version": "0.1",
        "report_id": "actor_gpu_001_review",
        "status": "review_complete",
        "request": "Measure the selected Actor GPU impact.",
        "experiment": {
            "schema_version": "0.1",
            "experiment_id": "actor_gpu_001"
        },
        "experiment_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "experiment_file_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "analyzed_by": {"provider": "ollama", "model": "gpt-oss:20b"},
        "summary": "ShadowDepths decreased while the selected Actor was hidden.",
        "primary_delta_id": "delta_000",
        "findings": [{
            "severity": "warning",
            "category": "impact_candidate",
            "evidence_delta_ids": ["delta_000"],
            "observation": "The normalized measured scope was lower in the hidden variant.",
            "recommendation": "Repeat the same controlled pair before changing the asset."
        }],
        "missing_capabilities": [],
        "modifies_editor_scene": false
    })JSON");
    const FString Path = FPaths::Combine(
        FPaths::ProjectIntermediateDir(),
        TEXT("RenderMasterActorGpuImpactReportTest.json"));
    TestTrue(TEXT("Actor GPU report fixture writes"), FFileHelper::SaveStringToFile(Json, *Path));
    FRenderMasterActorGpuImpactReport Report;
    FString Error;
    TestTrue(
        TEXT("Read-only Actor GPU report parses"),
        RenderMasterParseActorGpuImpactReportFile(Path, Report, Error));
    TestEqual(TEXT("Experiment identity is preserved"), Report.ExperimentId, FString(TEXT("actor_gpu_001")));
    TestEqual(TEXT("Primary delta is preserved"), Report.PrimaryDeltaId, FString(TEXT("delta_000")));
    TestEqual(TEXT("One cited delta is preserved"), Report.CitedDeltaIds.Num(), 1);
    if (Report.CitedDeltaIds.Num() == 1)
        TestEqual(TEXT("Cited delta identity is exact"), Report.CitedDeltaIds[0], FString(TEXT("delta_000")));
    TestFalse(TEXT("Actor GPU report cannot modify the Editor"), Report.bModifiesEditorScene);
    const FString Tampered = Json.Replace(
        TEXT("\"modifies_editor_scene\": false"),
        TEXT("\"modifies_editor_scene\": true"));
    TestTrue(TEXT("Tampered Actor GPU report fixture writes"), FFileHelper::SaveStringToFile(Tampered, *Path));
    TestFalse(
        TEXT("Scene-modifying Actor GPU report is rejected"),
        RenderMasterParseActorGpuImpactReportFile(Path, Report, Error));
    IFileManager::Get().Delete(*Path, false, true);
    return !HasAnyErrors();
}

#endif
