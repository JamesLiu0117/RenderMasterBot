#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "Editor.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/PointLightComponent.h"
#include "Engine/PointLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RenderMasterManifest.h"
#include "RenderMasterCameraAssistant.h"
#include "RenderMasterMaterialAssistant.h"
#include "RenderMasterLightAssistant.h"
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

#endif
