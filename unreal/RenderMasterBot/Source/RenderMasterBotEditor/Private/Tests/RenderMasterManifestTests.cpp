#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "Editor.h"
#include "Components/PointLightComponent.h"
#include "Engine/PointLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "RenderMasterManifest.h"
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

#endif
