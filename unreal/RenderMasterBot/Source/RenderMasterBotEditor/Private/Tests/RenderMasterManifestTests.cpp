#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "RenderMasterManifest.h"
#include "RenderMasterMaterialAssistant.h"

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

#endif
