#include "RenderMasterMaterialAssistant.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "RenderMasterWorkflowController.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
FString QuoteMaterialArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

FString ObjectPath(const UObject* Object)
{
    return Object != nullptr ? Object->GetPathName() : FString();
}

bool ReadObject(const TSharedPtr<FJsonObject>& Parent, const TCHAR* Field, TSharedPtr<FJsonObject>& Out)
{
    const TSharedPtr<FJsonObject>* Value = nullptr;
    if (!Parent.IsValid() || !Parent->TryGetObjectField(Field, Value) || Value == nullptr)
    {
        return false;
    }
    Out = *Value;
    return Out.IsValid();
}
}

bool FRenderMasterMaterialProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterMaterialProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read material proposal: %s"), *Filename);
        return false;
    }

    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterMaterialProposal::Parse(
    const FString& JsonText,
    FRenderMasterMaterialProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Material proposal is not valid JSON.");
        return false;
    }

    FRenderMasterMaterialProposal Parsed;
    if (!Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale))
    {
        OutError = TEXT("Material proposal is missing proposal_id, status, or rationale.");
        return false;
    }

    TSharedPtr<FJsonObject> Target;
    if (!ReadObject(Root, TEXT("target"), Target)
        || !Target->TryGetStringField(TEXT("actor_path"), Parsed.ActorPath)
        || !Target->TryGetStringField(TEXT("component_name"), Parsed.ComponentName)
        || !Target->TryGetStringField(TEXT("mesh_path"), Parsed.MeshPath))
    {
        OutError = TEXT("Material proposal target evidence is incomplete.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    if (Root->TryGetArrayField(TEXT("missing_capabilities"), Missing) && Missing != nullptr)
    {
        TArray<FString> Values;
        for (const TSharedPtr<FJsonValue>& Value : *Missing) Values.Add(Value->AsString());
        Parsed.MissingCapabilities = FString::Join(Values, TEXT(", "));
    }

    if (Parsed.Status == TEXT("proposed"))
    {
        TSharedPtr<FJsonObject> Slot;
        TSharedPtr<FJsonObject> Material;
        if (!ReadObject(Root, TEXT("selected_slot"), Slot)
            || !ReadObject(Root, TEXT("selected_material"), Material)
            || !Slot->TryGetNumberField(TEXT("slot_index"), Parsed.SlotIndex)
            || !Slot->TryGetStringField(TEXT("slot_name"), Parsed.SlotName)
            || !Material->TryGetStringField(TEXT("asset_id"), Parsed.MaterialAssetId)
            || !Material->TryGetStringField(TEXT("display_name"), Parsed.MaterialDisplayName)
            || !Material->TryGetStringField(TEXT("engine_path"), Parsed.MaterialPath)
            || !Material->TryGetNumberField(TEXT("similarity"), Parsed.Similarity))
        {
            OutError = TEXT("Proposed material action is missing its slot or material.");
            return false;
        }
        Slot->TryGetStringField(TEXT("current_material_path"), Parsed.CurrentMaterialPath);
    }
    else if (Parsed.Status != TEXT("unresolved"))
    {
        OutError = FString::Printf(TEXT("Unsupported material proposal status: %s"), *Parsed.Status);
        return false;
    }

    OutProposal = MoveTemp(Parsed);
    return true;
}

bool FRenderMasterExternalMaterialProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterExternalMaterialProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read external material proposal: %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterExternalMaterialProposal::Parse(
    const FString& JsonText,
    FRenderMasterExternalMaterialProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("External material proposal is not valid JSON.");
        return false;
    }

    FRenderMasterExternalMaterialProposal Parsed;
    if (!Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("provider_credit"), Parsed.ProviderCredit)
        || !Root->TryGetStringField(TEXT("provider_asset_id"), Parsed.ProviderAssetId)
        || !Root->TryGetStringField(TEXT("display_name"), Parsed.DisplayName)
        || !Root->TryGetStringField(TEXT("description"), Parsed.Description)
        || !Root->TryGetStringField(TEXT("source_url"), Parsed.SourceUrl)
        || !Root->TryGetStringField(TEXT("license"), Parsed.License)
        || !Root->TryGetStringField(TEXT("license_url"), Parsed.LicenseUrl)
        || !Root->TryGetStringField(TEXT("resolution"), Parsed.Resolution)
        || !Root->TryGetStringField(TEXT("image_format"), Parsed.ImageFormat)
        || !Root->TryGetStringField(TEXT("import_proposal_path"), Parsed.ImportProposalPath)
        || !Root->TryGetStringField(TEXT("import_proposal_sha256"), Parsed.ImportProposalSha256)
        || !Root->TryGetStringField(TEXT("destination_path"), Parsed.DestinationPath)
        || !Root->TryGetStringField(TEXT("material_name"), Parsed.MaterialName))
    {
        OutError = TEXT("External material proposal is missing approval evidence.");
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>* PlannedAssets = nullptr;
    if (Parsed.Status != TEXT("pending_approval")
        || Parsed.License != TEXT("CC0-1.0")
        || Parsed.ImportProposalSha256.Len() != 64
        || !Root->TryGetArrayField(TEXT("planned_asset_paths"), PlannedAssets)
        || PlannedAssets == nullptr
        || PlannedAssets->Num() != 5)
    {
        OutError = TEXT("External material proposal is not a bounded five-asset CC0 approval.");
        return false;
    }
    for (const TSharedPtr<FJsonValue>& Value : *PlannedAssets)
    {
        FString Path;
        if (!Value.IsValid() || !Value->TryGetString(Path) || !Path.StartsWith(TEXT("/Game/")))
        {
            OutError = TEXT("External material proposal contains an invalid Unreal asset path.");
            return false;
        }
        Parsed.PlannedAssetPaths.Add(Path);
    }
    OutProposal = MoveTemp(Parsed);
    return true;
}

FRenderMasterMaterialAssistant::FRenderMasterMaterialAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterMaterialAssistant::~FRenderMasterMaterialAssistant()
{
    Shutdown();
}

void FRenderMasterMaterialAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterMaterialAssistant::Tick),
        0.2f);
}

void FRenderMasterMaterialAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterMaterialAssistant::StartProposal(
    const FString& Prompt,
    UStaticMeshComponent* Component,
    int32 TargetSlotIndex)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a material request before preparing an action."));
        return false;
    }
    if (!WorkflowController.IsValid())
    {
        Fail(TEXT("RenderMasterBot runtime configuration is unavailable."));
        return false;
    }

    const FString Python = WorkflowController->GetPythonExecutable();
    const FString Catalog = WorkflowController->GetAssetCatalog();
    const FString Root = WorkflowController->GetWorkflowRoot();
    if (!FPaths::FileExists(Python) || !FPaths::FileExists(Catalog) || Root.IsEmpty())
    {
        Fail(TEXT("Configure an existing Python executable, asset catalog, and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Catalog.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }

    const FString ProposalId = FString::Printf(
        TEXT("material_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    RequestDirectory = FPaths::Combine(Root, TEXT("assistant-material"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(RequestDirectory, TEXT("selection_context.json"));
    ProposalOutputPath = FPaths::Combine(RequestDirectory, TEXT("material_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteSelectionContext(ContextPath, Component, TargetSlotIndex, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the assistant request.") : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-material-propose --prompt-file %s --context %s --assets %s --proposal-id %s --output %s"),
        *QuoteMaterialArgument(PromptPath),
        *QuoteMaterialArgument(ContextPath),
        *QuoteMaterialArgument(Catalog),
        *QuoteMaterialArgument(ProposalId),
        *QuoteMaterialArgument(ProposalOutputPath));
    Proposal = FRenderMasterMaterialProposal();
    ExternalProposal = FRenderMasterExternalMaterialProposal();
    bExternalProposal = false;
    ErrorText.Reset();
    ProcessLog.Reset();
    ProcessKind = ERenderMasterMaterialProcess::ProjectProposal;
    return LaunchPythonProcess(
        Arguments,
        TEXT("Searching catalog-verified project materials"),
        ERenderMasterMaterialAssistantState::Searching);
}

bool FRenderMasterMaterialAssistant::StartExternalProposal(
    const FString& Prompt,
    UStaticMeshComponent* Component,
    int32 TargetSlotIndex)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a material request before searching Poly Haven."));
        return false;
    }
    if (!WorkflowController.IsValid())
    {
        Fail(TEXT("RenderMasterBot runtime configuration is unavailable."));
        return false;
    }

    const FString Python = WorkflowController->GetPythonExecutable();
    const FString Catalog = WorkflowController->GetAssetCatalog();
    const FString Root = WorkflowController->GetWorkflowRoot();
    if (!FPaths::FileExists(Python) || !FPaths::FileExists(Catalog) || Root.IsEmpty())
    {
        Fail(TEXT("Configure an existing Python executable, asset catalog, and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Catalog.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }

    const FString ProposalId = FString::Printf(
        TEXT("external_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    RequestDirectory = FPaths::Combine(Root, TEXT("assistant-external-material"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(RequestDirectory, TEXT("selection_context.json"));
    const FString LibraryRoot = FPaths::Combine(Root, TEXT("material-library"));
    ProposalOutputPath = FPaths::Combine(RequestDirectory, TEXT("assistant_external_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteSelectionContext(ContextPath, Component, TargetSlotIndex, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the external material request.") : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-external-material-prepare --prompt-file %s --library-root %s --work-dir %s --proposal-id %s --output %s"),
        *QuoteMaterialArgument(PromptPath),
        *QuoteMaterialArgument(LibraryRoot),
        *QuoteMaterialArgument(RequestDirectory),
        *QuoteMaterialArgument(ProposalId),
        *QuoteMaterialArgument(ProposalOutputPath));
    Proposal = FRenderMasterMaterialProposal();
    ExternalProposal = FRenderMasterExternalMaterialProposal();
    bExternalProposal = false;
    ErrorText.Reset();
    ProcessLog.Reset();
    ProcessKind = ERenderMasterMaterialProcess::ExternalProposal;
    return LaunchPythonProcess(
        Arguments,
        TEXT("Searching and verifying one CC0 Poly Haven material"),
        ERenderMasterMaterialAssistantState::Searching);
}

bool FRenderMasterMaterialAssistant::LaunchPythonProcess(
    const FString& Arguments,
    const FString& Activity,
    ERenderMasterMaterialAssistantState ActiveState)
{
    if (!WorkflowController.IsValid()) return false;
    const FString Python = WorkflowController->GetPythonExecutable();
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        ProcessKind = ERenderMasterMaterialProcess::None;
        Fail(TEXT("Could not create assistant process pipes."));
        return false;
    }
    uint32 ProcessId = 0;
    ProcessHandle = FPlatformProcess::CreateProc(
        *Python,
        *Arguments,
        false,
        true,
        true,
        &ProcessId,
        0,
        nullptr,
        StdOutWrite,
        nullptr,
        StdErrWrite);
    if (!ProcessHandle.IsValid())
    {
        CloseProcessResources();
        ProcessKind = ERenderMasterMaterialProcess::None;
        Fail(FString::Printf(TEXT("Could not start Python: %s"), *Python));
        return false;
    }
    State = ActiveState;
    AppendLog(FString::Printf(TEXT("%s (process %u)."), *Activity, ProcessId));
    return true;
}

bool FRenderMasterMaterialAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTarget(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("No material proposal is ready to apply.") : Error);
        return false;
    }

    if (bExternalProposal)
    {
        return StartExternalImport();
    }
    return ApplyLoadedMaterial();
}

bool FRenderMasterMaterialAssistant::StartExternalImport()
{
    if (!WorkflowController.IsValid())
    {
        Fail(TEXT("RenderMasterBot runtime configuration is unavailable."));
        return false;
    }
    const FString Project = WorkflowController->GetProjectFile();
    const FString EngineRoot = WorkflowController->GetEngineRoot();
    const FString Catalog = WorkflowController->GetAssetCatalog();
    if (!FPaths::FileExists(Project)
        || !FPaths::FileExists(Catalog)
        || EngineRoot.IsEmpty()
        || !FPaths::FileExists(ExternalProposal.ImportProposalPath))
    {
        Fail(TEXT("The project, engine, catalog, or frozen import proposal is unavailable."));
        return false;
    }
    if (Project.Contains(TEXT("\""))
        || EngineRoot.Contains(TEXT("\""))
        || Catalog.Contains(TEXT("\""))
        || RequestDirectory.Contains(TEXT("\""))
        || ExternalProposal.ImportProposalPath.Contains(TEXT("\"")))
    {
        Fail(TEXT("External import runtime paths cannot contain a double quote."));
        return false;
    }

    const FString ImportOutput = FPaths::Combine(RequestDirectory, TEXT("unreal_import.json"));
    ExternalExecutionOutputPath = FPaths::Combine(RequestDirectory, TEXT("import_execution.json"));
    const FString ScanOutput = FPaths::Combine(RequestDirectory, TEXT("imported_asset_scan.json"));
    const FString SyncOutput = FPaths::Combine(RequestDirectory, TEXT("catalog_sync.json"));
    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot external-material-execute-import %s --engine-root %s --proposal %s --approve-sha256 %s --approved-by unreal_editor_operator --import-output %s --asset-catalog %s --scan-output %s --catalog-sync-output %s --output %s"),
        *QuoteMaterialArgument(Project),
        *QuoteMaterialArgument(EngineRoot),
        *QuoteMaterialArgument(ExternalProposal.ImportProposalPath),
        *QuoteMaterialArgument(ExternalProposal.ImportProposalSha256),
        *QuoteMaterialArgument(ImportOutput),
        *QuoteMaterialArgument(Catalog),
        *QuoteMaterialArgument(ScanOutput),
        *QuoteMaterialArgument(SyncOutput),
        *QuoteMaterialArgument(ExternalExecutionOutputPath));
    ProcessLog.Reset();
    ProcessKind = ERenderMasterMaterialProcess::ExternalImport;
    return LaunchPythonProcess(
        Arguments,
        TEXT("Importing five approved assets and updating the local catalog"),
        ERenderMasterMaterialAssistantState::Importing);
}

bool FRenderMasterMaterialAssistant::ApplyLoadedMaterial()
{
    FString Error;
    if (!RevalidateTarget(Error))
    {
        Fail(Error);
        return false;
    }

    FString MaterialObjectPath = Proposal.MaterialPath;
    if (!MaterialObjectPath.Contains(TEXT(".")))
    {
        MaterialObjectPath += TEXT(".") + FPackageName::GetLongPackageAssetName(MaterialObjectPath);
    }

    UMaterialInterface* Material = LoadObject<UMaterialInterface>(nullptr, *MaterialObjectPath);
    if (Material == nullptr)
    {
        Fail(FString::Printf(TEXT("Could not load proposed material: %s"), *Proposal.MaterialPath));
        return false;
    }

    UStaticMeshComponent* Component = TargetComponent.Get();
    AActor* Owner = Component->GetOwner();
    const FScopedTransaction Transaction(
        NSLOCTEXT("RenderMasterBot", "ApplyAssistantMaterial", "RenderMasterBot: Apply Material"));
    Owner->Modify();
    Component->Modify();
    Component->SetMaterial(Proposal.SlotIndex, Material);
    Component->PostEditChange();
    Component->MarkRenderStateDirty();
    Owner->MarkPackageDirty();
    State = ERenderMasterMaterialAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied %s to %s slot %s. Use Ctrl+Z to undo."),
        *Proposal.MaterialDisplayName,
        *Owner->GetActorLabel(),
        *Proposal.SlotName));
    return true;
}

void FRenderMasterMaterialAssistant::RejectProposal()
{
    if (State == ERenderMasterMaterialAssistantState::Searching)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterMaterialAssistantState::Rejected;
    AppendLog(bExternalProposal
        ? TEXT("External proposal rejected. Cached source maps remain outside the project; no Unreal Content asset was created.")
        : TEXT("Proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterMaterialAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterMaterialAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterMaterialAssistant::CanApply() const
{
    return State == ERenderMasterMaterialAssistantState::Proposed && TargetComponent.IsValid();
}

bool FRenderMasterMaterialAssistant::IsSearching() const
{
    return State == ERenderMasterMaterialAssistantState::Searching
        || State == ERenderMasterMaterialAssistantState::Importing;
}

FText FRenderMasterMaterialAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterMaterialAssistantState::Searching: return NSLOCTEXT("RenderMasterBot", "MaterialSearching", "Searching");
        case ERenderMasterMaterialAssistantState::Proposed: return NSLOCTEXT("RenderMasterBot", "MaterialProposed", "Approval required");
        case ERenderMasterMaterialAssistantState::Importing: return NSLOCTEXT("RenderMasterBot", "MaterialImporting", "Importing approved material");
        case ERenderMasterMaterialAssistantState::Unresolved: return NSLOCTEXT("RenderMasterBot", "MaterialUnresolved", "Unresolved");
        case ERenderMasterMaterialAssistantState::Failed: return NSLOCTEXT("RenderMasterBot", "MaterialFailed", "Failed");
        case ERenderMasterMaterialAssistantState::Applied: return NSLOCTEXT("RenderMasterBot", "MaterialApplied", "Applied");
        case ERenderMasterMaterialAssistantState::Rejected: return NSLOCTEXT("RenderMasterBot", "MaterialRejected", "Rejected");
        default: return NSLOCTEXT("RenderMasterBot", "MaterialReady", "Ready");
    }
}

FText FRenderMasterMaterialAssistant::GetApprovalButtonText() const
{
    return bExternalProposal
        ? NSLOCTEXT("RenderMasterBot", "ApproveExternalMaterial", "Approve Import & Apply")
        : NSLOCTEXT("RenderMasterBot", "ApproveProjectMaterial", "Approve & Apply Material");
}

FText FRenderMasterMaterialAssistant::GetSummaryText() const
{
    if (State == ERenderMasterMaterialAssistantState::Searching)
    {
        return ProcessKind == ERenderMasterMaterialProcess::ExternalProposal
            ? NSLOCTEXT("RenderMasterBot", "ExternalMaterialSearchingSummary", "Searching Poly Haven, downloading the best CC0 candidate to the local cache, and verifying four PBR map hashes. Unreal Content is not being changed.")
            : NSLOCTEXT("RenderMasterBot", "MaterialSearchingSummary", "Embedding the request and searching catalog-verified project materials...");
    }
    if (State == ERenderMasterMaterialAssistantState::Proposed)
    {
        if (bExternalProposal)
        {
            return FText::FromString(FString::Printf(
                TEXT("Target actor\n%s\n\nMaterial slot\n%d — %s\n\nExternal candidate\n%s\n%s\n\nSource\n%s\n%s\n%s\n\nVerified cache\n4 maps — %s %s\n\nApproval will create and save exactly 5 project assets:\n%s\n\nApproval SHA-256\n%s\n\nAfter import, the asset catalog and Chroma are updated and the new material is applied to this slot. The scene override supports Ctrl+Z; the newly created Content assets remain saved."),
                *Proposal.ActorPath,
                Proposal.SlotIndex,
                *Proposal.SlotName,
                *ExternalProposal.DisplayName,
                *ExternalProposal.Description,
                *ExternalProposal.ProviderCredit,
                *ExternalProposal.SourceUrl,
                *ExternalProposal.License,
                *ExternalProposal.Resolution,
                *ExternalProposal.ImageFormat.ToUpper(),
                *FString::Join(ExternalProposal.PlannedAssetPaths, TEXT("\n")),
                *ExternalProposal.ImportProposalSha256));
        }
        return FText::FromString(FString::Printf(
            TEXT("Target actor\n%s\n\nMaterial slot\n%d — %s\n\nCurrent material\n%s\n\nProposed material\n%s\n%s\n\nSemantic similarity\n%.3f\n\nThis edits the current scene but does not save it automatically. Ctrl+Z is supported."),
            *Proposal.ActorPath,
            Proposal.SlotIndex,
            *Proposal.SlotName,
            Proposal.CurrentMaterialPath.IsEmpty() ? TEXT("None") : *Proposal.CurrentMaterialPath,
            *Proposal.MaterialDisplayName,
            *Proposal.MaterialPath,
            Proposal.Similarity));
    }
    if (State == ERenderMasterMaterialAssistantState::Importing)
    {
        return NSLOCTEXT("RenderMasterBot", "ExternalMaterialImportingSummary", "The exact approved proposal is creating four textures and one connected Unreal material. When import succeeds, the catalog and Chroma index will be synchronized before the material is applied to the captured slot.");
    }
    if (State == ERenderMasterMaterialAssistantState::Unresolved)
    {
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMissing capability\n%s"),
            *Proposal.Rationale,
            *Proposal.MissingCapabilities));
    }
    if (State == ERenderMasterMaterialAssistantState::Failed) return FText::FromString(ErrorText);
    if (State == ERenderMasterMaterialAssistantState::Applied)
    {
        if (bExternalProposal)
        {
            return FText::FromString(FString::Printf(
                TEXT("Imported, indexed, and applied %s to slot %s. Five CC0 Content assets were saved. The level was not saved automatically; use Ctrl+Z to undo the slot override."),
                *Proposal.MaterialDisplayName,
                *Proposal.SlotName));
        }
        return FText::FromString(FString::Printf(
            TEXT("Applied %s to slot %s. The level was not saved automatically. Use Ctrl+Z to undo."),
            *Proposal.MaterialDisplayName,
            *Proposal.SlotName));
    }
    if (State == ERenderMasterMaterialAssistantState::Rejected)
    {
        return bExternalProposal
            ? NSLOCTEXT("RenderMasterBot", "ExternalMaterialRejectedSummary", "The external proposal was rejected. Verified downloads remain in the workstation cache, but no Unreal Content asset or scene change was created.")
            : NSLOCTEXT("RenderMasterBot", "MaterialRejectedSummary", "The proposal was rejected. No scene change was applied.");
    }
    return NSLOCTEXT("RenderMasterBot", "MaterialReadySummary", "Select one Static Mesh Actor and describe the material appearance you want.");
}

FText FRenderMasterMaterialAssistant::GetLogText() const
{
    return FText::FromString(ProcessLog);
}

FLinearColor FRenderMasterMaterialAssistant::GetStateColor() const
{
    if (State == ERenderMasterMaterialAssistantState::Proposed) return FLinearColor(0.12f, 0.55f, 0.85f);
    if (State == ERenderMasterMaterialAssistantState::Importing) return FLinearColor(0.55f, 0.35f, 0.85f);
    if (State == ERenderMasterMaterialAssistantState::Applied) return FLinearColor(0.12f, 0.62f, 0.38f);
    if (State == ERenderMasterMaterialAssistantState::Failed) return FLinearColor(0.9f, 0.2f, 0.2f);
    if (State == ERenderMasterMaterialAssistantState::Unresolved) return FLinearColor(0.95f, 0.55f, 0.15f);
    return FLinearColor(0.2f, 0.23f, 0.28f);
}

bool FRenderMasterMaterialAssistant::Tick(float DeltaTime)
{
    ReadProcessOutput();
    if (ProcessHandle.IsValid() && !FPlatformProcess::IsProcRunning(ProcessHandle)) FinishProcess();
    return true;
}

void FRenderMasterMaterialAssistant::FinishProcess()
{
    ReadProcessOutput();
    const ERenderMasterMaterialProcess CompletedProcess = ProcessKind;
    ProcessKind = ERenderMasterMaterialProcess::None;
    int32 ExitCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ExitCode);
    FPlatformProcess::CloseProc(ProcessHandle);
    ProcessHandle.Reset();
    FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = StdOutWrite = StdErrRead = StdErrWrite = nullptr;

    if (ExitCode != 0)
    {
        Fail(FString::Printf(TEXT("Material assistant process exited with code %d.\n%s"), ExitCode, *ProcessLog));
        return;
    }

    FString Error;
    if (CompletedProcess == ERenderMasterMaterialProcess::ProjectProposal)
    {
        if (!FRenderMasterMaterialProposal::LoadFromFile(ProposalOutputPath, Proposal, Error))
        {
            Fail(Error);
            return;
        }
        bExternalProposal = false;
        State = Proposal.Status == TEXT("proposed")
            ? ERenderMasterMaterialAssistantState::Proposed
            : ERenderMasterMaterialAssistantState::Unresolved;
        return;
    }
    if (CompletedProcess == ERenderMasterMaterialProcess::ExternalProposal)
    {
        if (!FRenderMasterExternalMaterialProposal::LoadFromFile(
                ProposalOutputPath,
                ExternalProposal,
                Error))
        {
            Fail(Error);
            return;
        }
        Proposal.ProposalId = ExternalProposal.ProposalId;
        Proposal.Status = TEXT("proposed");
        Proposal.ActorPath = CapturedActorPath;
        Proposal.ComponentName = CapturedComponentName;
        Proposal.MeshPath = CapturedMeshPath;
        Proposal.SlotIndex = CapturedSlotIndex;
        Proposal.SlotName = CapturedSlotName;
        Proposal.CurrentMaterialPath = CapturedCurrentMaterialPath;
        Proposal.MaterialAssetId = ExternalProposal.ProviderAssetId;
        Proposal.MaterialDisplayName = ExternalProposal.DisplayName;
        Proposal.MaterialPath = FString::Printf(
            TEXT("%s/%s"),
            *ExternalProposal.DestinationPath,
            *ExternalProposal.MaterialName);
        bExternalProposal = true;
        State = ERenderMasterMaterialAssistantState::Proposed;
        return;
    }
    if (CompletedProcess == ERenderMasterMaterialProcess::ExternalImport)
    {
        if (!FPaths::FileExists(ExternalExecutionOutputPath))
        {
            Fail(TEXT("The approved import finished without validated execution evidence."));
            return;
        }
        FAssetRegistryModule& AssetRegistryModule =
            FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
        TArray<FString> PathsToScan;
        PathsToScan.Add(ExternalProposal.DestinationPath);
        AssetRegistryModule.Get().ScanPathsSynchronous(PathsToScan, true);
        ApplyLoadedMaterial();
        return;
    }
    Fail(TEXT("Material assistant completed an unknown process."));
}

void FRenderMasterMaterialAssistant::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterMaterialAssistant::CloseProcessResources()
{
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::CloseProc(ProcessHandle);
        ProcessHandle.Reset();
    }
    if (StdOutRead != nullptr || StdOutWrite != nullptr) FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    if (StdErrRead != nullptr || StdErrWrite != nullptr) FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = StdOutWrite = StdErrRead = StdErrWrite = nullptr;
    ProcessKind = ERenderMasterMaterialProcess::None;
}

void FRenderMasterMaterialAssistant::AppendLog(const FString& Text)
{
    if (Text.IsEmpty()) return;
    ProcessLog += Text;
    if (!ProcessLog.EndsWith(TEXT("\n"))) ProcessLog += TEXT("\n");
    if (ProcessLog.Len() > 12000) ProcessLog.RightInline(12000, EAllowShrinking::No);
}

void FRenderMasterMaterialAssistant::Fail(const FString& Error)
{
    ErrorText = Error;
    State = ERenderMasterMaterialAssistantState::Failed;
    AppendLog(Error);
}

bool FRenderMasterMaterialAssistant::WriteSelectionContext(
    const FString& Filename,
    UStaticMeshComponent* Component,
    int32 TargetSlotIndex,
    FString& OutError)
{
    if (Component == nullptr || Component->GetOwner() == nullptr || Component->GetStaticMesh() == nullptr)
    {
        OutError = TEXT("Select one Actor with one valid Static Mesh Component.");
        return false;
    }

    AActor* Owner = Component->GetOwner();
    UStaticMesh* Mesh = Component->GetStaticMesh();
    const TArray<FStaticMaterial>& StaticMaterials = Mesh->GetStaticMaterials();
    if (StaticMaterials.IsEmpty())
    {
        OutError = TEXT("The selected Static Mesh has no material slots.");
        return false;
    }
    if (!StaticMaterials.IsValidIndex(TargetSlotIndex))
    {
        OutError = TEXT("Choose a valid material slot before preparing the action.");
        return false;
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Root->SetStringField(TEXT("level_path"), Owner->GetWorld()->GetPackage()->GetName());
    Root->SetStringField(TEXT("actor_name"), Owner->GetActorLabel());
    Root->SetStringField(TEXT("actor_path"), Owner->GetPathName());
    Root->SetStringField(TEXT("component_name"), Component->GetName());
    Root->SetStringField(TEXT("mesh_path"), Mesh->GetPathName());
    Root->SetNumberField(TEXT("target_slot_index"), TargetSlotIndex);

    TArray<TSharedPtr<FJsonValue>> Slots;
    for (int32 Index = 0; Index < StaticMaterials.Num(); ++Index)
    {
        TSharedRef<FJsonObject> Slot = MakeShared<FJsonObject>();
        Slot->SetNumberField(TEXT("slot_index"), Index);
        const FName SlotName = StaticMaterials[Index].MaterialSlotName;
        Slot->SetStringField(
            TEXT("slot_name"),
            SlotName.IsNone() ? FString::Printf(TEXT("Material_%d"), Index) : SlotName.ToString());
        if (const UMaterialInterface* Current = Component->GetMaterial(Index))
        {
            Slot->SetStringField(TEXT("current_material_path"), Current->GetPathName());
        }
        Slots.Add(MakeShared<FJsonValueObject>(Slot));
    }
    Root->SetArrayField(TEXT("material_slots"), Slots);

    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(TEXT("Could not write selection context: %s"), *Filename);
        return false;
    }

    TargetComponent = Component;
    CapturedActorPath = Owner->GetPathName();
    CapturedComponentName = Component->GetName();
    CapturedMeshPath = Mesh->GetPathName();
    CapturedSlotIndex = TargetSlotIndex;
    const FName CapturedName = StaticMaterials[TargetSlotIndex].MaterialSlotName;
    CapturedSlotName = CapturedName.IsNone()
        ? FString::Printf(TEXT("Material_%d"), TargetSlotIndex)
        : CapturedName.ToString();
    CapturedCurrentMaterialPath = ObjectPath(Component->GetMaterial(TargetSlotIndex));
    return true;
}

bool FRenderMasterMaterialAssistant::RevalidateTarget(FString& OutError) const
{
    UStaticMeshComponent* Component = TargetComponent.Get();
    if (Component == nullptr || Component->GetOwner() == nullptr || Component->GetStaticMesh() == nullptr)
    {
        OutError = TEXT("The captured target no longer exists.");
        return false;
    }
    if (Component->GetOwner()->GetPathName() != CapturedActorPath
        || Component->GetName() != CapturedComponentName
        || Component->GetStaticMesh()->GetPathName() != CapturedMeshPath)
    {
        OutError = TEXT("The selected Actor or mesh changed after the proposal was created.");
        return false;
    }
    if (Proposal.SlotIndex != CapturedSlotIndex)
    {
        OutError = TEXT("The proposal does not match the explicitly selected material slot.");
        return false;
    }
    if (Proposal.SlotIndex < 0 || Proposal.SlotIndex >= Component->GetNumMaterials())
    {
        OutError = TEXT("The proposed material slot no longer exists.");
        return false;
    }
    if (ObjectPath(Component->GetMaterial(Proposal.SlotIndex)) != CapturedCurrentMaterialPath)
    {
        OutError = TEXT("The target material changed after the proposal was created. Prepare a new action.");
        return false;
    }
    return true;
}
