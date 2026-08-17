#include "RenderMasterTransformAssistant.h"

#include "Components/SceneComponent.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RenderMasterWorkflowController.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
FString QuoteTransformArgument(const FString& Value)
{
    return FString::Printf(TEXT("\"%s\""), *Value);
}

bool ReadObject(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    TSharedPtr<FJsonObject>& Out)
{
    const TSharedPtr<FJsonObject>* Value = nullptr;
    if (!Parent.IsValid() || !Parent->TryGetObjectField(Field, Value) || Value == nullptr)
    {
        return false;
    }
    Out = *Value;
    return Out.IsValid();
}

bool ReadVector(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FVector& Out)
{
    TSharedPtr<FJsonObject> Value;
    double X = 0.0;
    double Y = 0.0;
    double Z = 0.0;
    if (!ReadObject(Parent, Field, Value)
        || !Value->TryGetNumberField(TEXT("x"), X)
        || !Value->TryGetNumberField(TEXT("y"), Y)
        || !Value->TryGetNumberField(TEXT("z"), Z)
        || !FMath::IsFinite(X)
        || !FMath::IsFinite(Y)
        || !FMath::IsFinite(Z))
    {
        return false;
    }
    Out = FVector(X, Y, Z);
    return true;
}

bool ReadSnapshot(
    const TSharedPtr<FJsonObject>& Parent,
    const TCHAR* Field,
    FRenderMasterTransformSnapshot& Out)
{
    TSharedPtr<FJsonObject> Snapshot;
    FVector RotationAxes;
    if (!ReadObject(Parent, Field, Snapshot)
        || !ReadVector(Snapshot, TEXT("location_cm"), Out.Location)
        || !ReadVector(Snapshot, TEXT("rotation_deg"), RotationAxes)
        || !ReadVector(Snapshot, TEXT("scale"), Out.Scale))
    {
        return false;
    }
    Out.Rotation = FRotator(RotationAxes.Y, RotationAxes.Z, RotationAxes.X);
    return true;
}

FRenderMasterTransformSnapshot SnapshotActor(const AActor* Actor)
{
    FRenderMasterTransformSnapshot Snapshot;
    if (Actor != nullptr)
    {
        const FTransform Transform = Actor->GetActorTransform();
        Snapshot.Location = Transform.GetLocation();
        Snapshot.Rotation = Transform.Rotator();
        Snapshot.Scale = Transform.GetScale3D();
    }
    return Snapshot;
}

bool RotationsMatch(const FRotator& A, const FRotator& B, double Tolerance = 0.01)
{
    return FMath::Abs(FMath::FindDeltaAngleDegrees(A.Roll, B.Roll)) <= Tolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Pitch, B.Pitch)) <= Tolerance
        && FMath::Abs(FMath::FindDeltaAngleDegrees(A.Yaw, B.Yaw)) <= Tolerance;
}

bool SnapshotsMatch(
    const FRenderMasterTransformSnapshot& A,
    const FRenderMasterTransformSnapshot& B)
{
    return A.Location.Equals(B.Location, 0.01)
        && RotationsMatch(A.Rotation, B.Rotation)
        && A.Scale.Equals(B.Scale, 0.0001);
}

TSharedRef<FJsonObject> VectorJson(const FVector& Value)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("x"), Value.X);
    Json->SetNumberField(TEXT("y"), Value.Y);
    Json->SetNumberField(TEXT("z"), Value.Z);
    return Json;
}

TSharedRef<FJsonObject> SnapshotJson(const FRenderMasterTransformSnapshot& Snapshot)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetObjectField(TEXT("location_cm"), VectorJson(Snapshot.Location));
    Json->SetObjectField(
        TEXT("rotation_deg"),
        VectorJson(FVector(Snapshot.Rotation.Roll, Snapshot.Rotation.Pitch, Snapshot.Rotation.Yaw)));
    Json->SetObjectField(TEXT("scale"), VectorJson(Snapshot.Scale));
    return Json;
}

FString SnapshotText(const FRenderMasterTransformSnapshot& Snapshot)
{
    return FString::Printf(
        TEXT("Location  X %.2f  Y %.2f  Z %.2f cm\nRotation  Roll %.2f  Pitch %.2f  Yaw %.2f deg\nScale     X %.3f  Y %.3f  Z %.3f"),
        Snapshot.Location.X,
        Snapshot.Location.Y,
        Snapshot.Location.Z,
        Snapshot.Rotation.Roll,
        Snapshot.Rotation.Pitch,
        Snapshot.Rotation.Yaw,
        Snapshot.Scale.X,
        Snapshot.Scale.Y,
        Snapshot.Scale.Z);
}

FVector SnapshotChannel(
    const FRenderMasterTransformSnapshot& Snapshot,
    const FString& Channel)
{
    if (Channel == TEXT("location")) return Snapshot.Location;
    if (Channel == TEXT("rotation"))
    {
        return FVector(Snapshot.Rotation.Roll, Snapshot.Rotation.Pitch, Snapshot.Rotation.Yaw);
    }
    return Snapshot.Scale;
}

bool IsBoundedTransform(
    const FRenderMasterTransformSnapshot& Before,
    const FRenderMasterTransformSnapshot& After)
{
    constexpr double MaxLocation = 10000000.0;
    constexpr double MaxLocationDelta = 1000000.0;
    constexpr double MinAbsScale = 0.01;
    constexpr double MaxAbsScale = 100.0;
    for (int32 Axis = 0; Axis < 3; ++Axis)
    {
        if (FMath::Abs(After.Location[Axis]) > MaxLocation
            || FMath::Abs(After.Location[Axis] - Before.Location[Axis]) > MaxLocationDelta)
        {
            return false;
        }
        const double AbsScale = FMath::Abs(After.Scale[Axis]);
        if (AbsScale < MinAbsScale || AbsScale > MaxAbsScale) return false;
    }
    return true;
}
}

bool FRenderMasterTransformProposal::LoadFromFile(
    const FString& Filename,
    FRenderMasterTransformProposal& OutProposal,
    FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read Transform proposal: %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutProposal, OutError);
}

bool FRenderMasterTransformProposal::Parse(
    const FString& JsonText,
    FRenderMasterTransformProposal& OutProposal,
    FString& OutError)
{
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        OutError = TEXT("Transform proposal is not valid JSON.");
        return false;
    }

    FRenderMasterTransformProposal Parsed;
    FString CoordinateSpace;
    bool bModifiesScene = false;
    bool bAutoSave = true;
    bool bUndoSupported = false;
    if (!Root->TryGetStringField(TEXT("proposal_id"), Parsed.ProposalId)
        || !Root->TryGetStringField(TEXT("status"), Parsed.Status)
        || !Root->TryGetStringField(TEXT("coordinate_space"), CoordinateSpace)
        || !Root->TryGetStringField(TEXT("rationale"), Parsed.Rationale)
        || !Root->TryGetBoolField(TEXT("modifies_editor_scene"), bModifiesScene)
        || !Root->TryGetBoolField(TEXT("auto_save"), bAutoSave)
        || !Root->TryGetBoolField(TEXT("undo_supported"), bUndoSupported)
        || CoordinateSpace != TEXT("world")
        || !bModifiesScene
        || bAutoSave
        || !bUndoSupported)
    {
        OutError = TEXT("Transform proposal is missing its safe world-space approval flags.");
        return false;
    }

    TSharedPtr<FJsonObject> Target;
    TSharedPtr<FJsonObject> TargetSnapshot;
    if (!ReadObject(Root, TEXT("target"), Target)
        || !Target->TryGetStringField(TEXT("actor_name"), Parsed.ActorName)
        || !Target->TryGetStringField(TEXT("actor_path"), Parsed.ActorPath)
        || !Target->TryGetStringField(TEXT("actor_class"), Parsed.ActorClass)
        || !ReadObject(Target, TEXT("transform"), TargetSnapshot)
        || !ReadSnapshot(Root, TEXT("before"), Parsed.Before))
    {
        OutError = TEXT("Transform proposal target or before evidence is incomplete.");
        return false;
    }
    Target->TryGetStringField(TEXT("actor_guid"), Parsed.ActorGuid);

    FRenderMasterTransformSnapshot TargetEvidence;
    FVector RotationAxes;
    if (!ReadVector(TargetSnapshot, TEXT("location_cm"), TargetEvidence.Location)
        || !ReadVector(TargetSnapshot, TEXT("rotation_deg"), RotationAxes)
        || !ReadVector(TargetSnapshot, TEXT("scale"), TargetEvidence.Scale))
    {
        OutError = TEXT("Transform proposal contains invalid target Transform evidence.");
        return false;
    }
    TargetEvidence.Rotation = FRotator(RotationAxes.Y, RotationAxes.Z, RotationAxes.X);
    if (!SnapshotsMatch(TargetEvidence, Parsed.Before))
    {
        OutError = TEXT("Transform proposal before values do not match target evidence.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* Missing = nullptr;
    if (Root->TryGetArrayField(TEXT("missing_capabilities"), Missing) && Missing != nullptr)
    {
        TArray<FString> Values;
        for (const TSharedPtr<FJsonValue>& Value : *Missing)
        {
            FString Item;
            if (Value.IsValid() && Value->TryGetString(Item) && !Item.IsEmpty()) Values.Add(Item);
        }
        Parsed.MissingCapabilities = FString::Join(Values, TEXT(", "));
    }

    if (Parsed.Status == TEXT("proposed"))
    {
        const TArray<TSharedPtr<FJsonValue>>* Changes = nullptr;
        if (!ReadSnapshot(Root, TEXT("after"), Parsed.After)
            || !Root->TryGetArrayField(TEXT("changes"), Changes)
            || Changes == nullptr
            || Changes->IsEmpty()
            || Changes->Num() > 3
            || !Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Proposed Transform action is missing bounded Before/After changes.");
            return false;
        }
        if (!IsBoundedTransform(Parsed.Before, Parsed.After))
        {
            OutError = TEXT("Proposed Transform action exceeds the Editor safety bounds.");
            return false;
        }
        TArray<FString> ChangeLines;
        TSet<FString> SeenChannels;
        for (const TSharedPtr<FJsonValue>& Value : *Changes)
        {
            const TSharedPtr<FJsonObject>* Change = nullptr;
            FString Channel;
            FString Operation;
            const TArray<TSharedPtr<FJsonValue>>* Axes = nullptr;
            if (!Value.IsValid()
                || !Value->TryGetObject(Change)
                || Change == nullptr
                || !(*Change)->TryGetStringField(TEXT("channel"), Channel)
                || !(*Change)->TryGetStringField(TEXT("operation"), Operation)
                || !(*Change)->TryGetArrayField(TEXT("axes"), Axes)
                || Axes == nullptr
                || Axes->IsEmpty())
            {
                OutError = TEXT("Transform proposal contains an invalid change summary.");
                return false;
            }
            Channel.ToLowerInline();
            Operation.ToLowerInline();
            const bool bAllowedOperation =
                ((Channel == TEXT("location") || Channel == TEXT("rotation"))
                    && (Operation == TEXT("set") || Operation == TEXT("add")))
                || (Channel == TEXT("scale")
                    && (Operation == TEXT("set") || Operation == TEXT("multiply")));
            if (!bAllowedOperation || SeenChannels.Contains(Channel))
            {
                OutError = TEXT("Transform proposal contains an unsupported or repeated channel operation.");
                return false;
            }
            SeenChannels.Add(Channel);

            FVector ChangeBefore;
            FVector ChangeAfter;
            const FVector ProposalBefore = SnapshotChannel(Parsed.Before, Channel);
            const FVector ProposalAfter = SnapshotChannel(Parsed.After, Channel);
            if (!ReadVector(*Change, TEXT("before"), ChangeBefore)
                || !ReadVector(*Change, TEXT("after"), ChangeAfter)
                || !ChangeBefore.Equals(ProposalBefore, 0.000001)
                || !ChangeAfter.Equals(ProposalAfter, 0.000001))
            {
                OutError = TEXT("Transform change evidence does not match the proposal Before/After values.");
                return false;
            }
            TArray<FString> AxisNames;
            TSet<FString> SeenAxes;
            for (const TSharedPtr<FJsonValue>& Axis : *Axes)
            {
                FString AxisName;
                if (!Axis.IsValid() || !Axis->TryGetString(AxisName))
                {
                    OutError = TEXT("Transform proposal contains an invalid axis.");
                    return false;
                }
                AxisName.ToLowerInline();
                if ((AxisName != TEXT("x") && AxisName != TEXT("y") && AxisName != TEXT("z"))
                    || SeenAxes.Contains(AxisName))
                {
                    OutError = TEXT("Transform proposal contains an unsupported or repeated axis.");
                    return false;
                }
                SeenAxes.Add(AxisName);
                AxisNames.Add(AxisName.ToUpper());
            }
            const bool bExpectedX = !FMath::IsNearlyEqual(ChangeBefore.X, ChangeAfter.X, 0.000001);
            const bool bExpectedY = !FMath::IsNearlyEqual(ChangeBefore.Y, ChangeAfter.Y, 0.000001);
            const bool bExpectedZ = !FMath::IsNearlyEqual(ChangeBefore.Z, ChangeAfter.Z, 0.000001);
            if (SeenAxes.Contains(TEXT("x")) != bExpectedX
                || SeenAxes.Contains(TEXT("y")) != bExpectedY
                || SeenAxes.Contains(TEXT("z")) != bExpectedZ)
            {
                OutError = TEXT("Transform change axes do not match observable Before/After differences.");
                return false;
            }
            ChangeLines.Add(FString::Printf(
                TEXT("%s: %s %s"),
                *Channel,
                *Operation,
                *FString::Join(AxisNames, TEXT(", "))));
        }
        for (const FString& Channel : {FString(TEXT("location")), FString(TEXT("rotation")), FString(TEXT("scale"))})
        {
            const bool bActuallyChanged = !SnapshotChannel(Parsed.Before, Channel).Equals(
                SnapshotChannel(Parsed.After, Channel),
                0.000001);
            if (SeenChannels.Contains(Channel) != bActuallyChanged)
            {
                OutError = TEXT("Transform changes do not cover every changed channel exactly once.");
                return false;
            }
        }
        Parsed.ChangeSummary = FString::Join(ChangeLines, TEXT("\n"));
    }
    else if (Parsed.Status == TEXT("unresolved"))
    {
        if (Parsed.MissingCapabilities.IsEmpty())
        {
            OutError = TEXT("Unresolved Transform action must name a missing capability.");
            return false;
        }
    }
    else
    {
        OutError = FString::Printf(TEXT("Unsupported Transform proposal status: %s"), *Parsed.Status);
        return false;
    }

    OutProposal = MoveTemp(Parsed);
    return true;
}

bool RenderMasterApplyActorTransform(
    AActor* Actor,
    const FRenderMasterTransformSnapshot& After,
    FString& OutError,
    bool bMarkPackageDirty)
{
    if (Actor == nullptr)
    {
        OutError = TEXT("The target Actor no longer exists.");
        return false;
    }

    FScopedTransaction Transaction(
        NSLOCTEXT("RenderMasterBot", "ApplyAssistantTransform", "RenderMasterBot: Apply Actor Transform"));
    Actor->Modify();
    if (USceneComponent* RootComponent = Actor->GetRootComponent()) RootComponent->Modify();
    const FTransform NewTransform(After.Rotation, After.Location, After.Scale);
    if (!Actor->SetActorTransform(NewTransform, false, nullptr, ETeleportType::TeleportPhysics))
    {
        Transaction.Cancel();
        OutError = TEXT("Unreal rejected the proposed Actor Transform.");
        return false;
    }
    Actor->PostEditMove(true);
    if (bMarkPackageDirty) Actor->MarkPackageDirty();
    if (GEditor != nullptr) GEditor->RedrawLevelEditingViewports();
    return true;
}

FRenderMasterTransformAssistant::FRenderMasterTransformAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController))
{
}

FRenderMasterTransformAssistant::~FRenderMasterTransformAssistant()
{
    Shutdown();
}

void FRenderMasterTransformAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(AsShared(), &FRenderMasterTransformAssistant::Tick),
        0.2f);
}

void FRenderMasterTransformAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
}

bool FRenderMasterTransformAssistant::StartProposal(const FString& Prompt, AActor* Actor)
{
    if (!CanStart()) return false;
    const FString CleanPrompt = Prompt.TrimStartAndEnd();
    if (CleanPrompt.IsEmpty())
    {
        Fail(TEXT("Enter a Transform request before preparing an action."));
        return false;
    }
    if (!WorkflowController.IsValid())
    {
        Fail(TEXT("RenderMasterBot runtime configuration is unavailable."));
        return false;
    }
    const FString Python = WorkflowController->GetPythonExecutable();
    const FString Root = WorkflowController->GetWorkflowRoot();
    if (!FPaths::FileExists(Python) || Root.IsEmpty())
    {
        Fail(TEXT("Configure an existing Python executable and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }

    const FString ProposalId = FString::Printf(
        TEXT("transform_%s"),
        *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S_%s")));
    const FString RequestDirectory = FPaths::Combine(Root, TEXT("assistant-transform"), ProposalId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString PromptPath = FPaths::Combine(RequestDirectory, TEXT("request.txt"));
    const FString ContextPath = FPaths::Combine(RequestDirectory, TEXT("actor_transform_context.json"));
    ProposalOutputPath = FPaths::Combine(RequestDirectory, TEXT("transform_proposal.json"));

    FString Error;
    if (!FFileHelper::SaveStringToFile(
            CleanPrompt,
            *PromptPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !WriteActorContext(ContextPath, Actor, Error))
    {
        Fail(Error.IsEmpty() ? TEXT("Could not write the Transform request.") : Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-transform-propose --prompt-file %s --context %s --proposal-id %s --output %s"),
        *QuoteTransformArgument(PromptPath),
        *QuoteTransformArgument(ContextPath),
        *QuoteTransformArgument(ProposalId),
        *QuoteTransformArgument(ProposalOutputPath));

    Proposal = FRenderMasterTransformProposal();
    ErrorText.Reset();
    ProcessLog.Reset();
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create Transform assistant process pipes."));
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
        Fail(FString::Printf(TEXT("Could not start Python: %s"), *Python));
        return false;
    }
    State = ERenderMasterTransformAssistantState::Planning;
    AppendLog(FString::Printf(TEXT("Preparing a bounded world Transform proposal (process %u)."), ProcessId));
    return true;
}

bool FRenderMasterTransformAssistant::WriteActorContext(
    const FString& Filename,
    AActor* Actor,
    FString& OutError)
{
    if (Actor == nullptr || Actor->GetWorld() == nullptr || Actor->IsTemplate())
    {
        OutError = TEXT("Select exactly one editable Actor in the current level.");
        return false;
    }
    if (!Actor->IsEditable() || Actor->IsLockLocation())
    {
        OutError = TEXT("The selected Actor is not editable or its location is locked.");
        return false;
    }

    CapturedActorPath = Actor->GetPathName();
    CapturedActorClass = Actor->GetClass()->GetName();
    CapturedActorGuid = Actor->GetActorGuid().IsValid()
        ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    CapturedTransform = SnapshotActor(Actor);
    TargetActor = Actor;

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Root->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Root->SetStringField(TEXT("level_path"), Actor->GetWorld()->GetPackage()->GetName());
    Root->SetStringField(TEXT("actor_name"), Actor->GetActorLabel());
    Root->SetStringField(TEXT("actor_path"), CapturedActorPath);
    Root->SetStringField(TEXT("actor_class"), CapturedActorClass);
    if (!CapturedActorGuid.IsEmpty()) Root->SetStringField(TEXT("actor_guid"), CapturedActorGuid);

    FString Mobility = TEXT("none");
    if (const USceneComponent* RootComponent = Actor->GetRootComponent())
    {
        Root->SetStringField(TEXT("root_component_name"), RootComponent->GetName());
        switch (RootComponent->Mobility)
        {
            case EComponentMobility::Static: Mobility = TEXT("static"); break;
            case EComponentMobility::Stationary: Mobility = TEXT("stationary"); break;
            case EComponentMobility::Movable: Mobility = TEXT("movable"); break;
            default: break;
        }
    }
    Root->SetStringField(TEXT("root_mobility"), Mobility);
    Root->SetBoolField(TEXT("is_editable"), true);
    Root->SetBoolField(TEXT("is_locked"), false);
    Root->SetObjectField(TEXT("transform"), SnapshotJson(CapturedTransform));

    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer)
        || !FFileHelper::SaveStringToFile(
            JsonText,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = FString::Printf(TEXT("Could not write Actor Transform context: %s"), *Filename);
        TargetActor.Reset();
        return false;
    }
    return true;
}

bool FRenderMasterTransformAssistant::ApplyProposal()
{
    FString Error;
    if (!CanApply() || !RevalidateTarget(Error))
    {
        Fail(Error.IsEmpty() ? TEXT("No Transform proposal is ready to apply.") : Error);
        return false;
    }

    AActor* Actor = TargetActor.Get();
    if (!RenderMasterApplyActorTransform(Actor, Proposal.After, Error))
    {
        Fail(Error);
        return false;
    }
    State = ERenderMasterTransformAssistantState::Applied;
    AppendLog(FString::Printf(
        TEXT("Applied the approved world Transform to %s. The level was not saved; use Ctrl+Z to undo."),
        *Actor->GetActorLabel()));
    return true;
}

bool FRenderMasterTransformAssistant::RevalidateTarget(FString& OutError) const
{
    AActor* Actor = TargetActor.Get();
    if (Actor == nullptr || Actor->GetWorld() == nullptr)
    {
        OutError = TEXT("The captured Actor no longer exists.");
        return false;
    }
    const FString CurrentGuid = Actor->GetActorGuid().IsValid()
        ? Actor->GetActorGuid().ToString(EGuidFormats::Digits)
        : FString();
    if (Actor->GetPathName() != CapturedActorPath
        || Actor->GetClass()->GetName() != CapturedActorClass
        || CurrentGuid != CapturedActorGuid
        || Proposal.ActorPath != CapturedActorPath
        || Proposal.ActorClass != CapturedActorClass
        || Proposal.ActorGuid != CapturedActorGuid)
    {
        OutError = TEXT("The selected Actor identity changed after the proposal was created.");
        return false;
    }
    if (!Actor->IsEditable() || Actor->IsLockLocation())
    {
        OutError = TEXT("The captured Actor is no longer editable or is now locked.");
        return false;
    }
    if (!SnapshotsMatch(Proposal.Before, CapturedTransform)
        || !SnapshotsMatch(SnapshotActor(Actor), CapturedTransform))
    {
        OutError = TEXT("The Actor Transform changed after the proposal was created. Prepare a new action.");
        return false;
    }
    return true;
}

void FRenderMasterTransformAssistant::RejectProposal()
{
    if (State == ERenderMasterTransformAssistantState::Planning)
    {
        Cancel();
        CloseProcessResources();
    }
    State = ERenderMasterTransformAssistantState::Rejected;
    AppendLog(TEXT("Transform proposal rejected. No Editor scene change was applied."));
}

void FRenderMasterTransformAssistant::Cancel()
{
    if (ProcessHandle.IsValid()) FPlatformProcess::TerminateProc(ProcessHandle, true);
}

bool FRenderMasterTransformAssistant::CanStart() const
{
    return !ProcessHandle.IsValid();
}

bool FRenderMasterTransformAssistant::CanApply() const
{
    return State == ERenderMasterTransformAssistantState::Proposed && TargetActor.IsValid();
}

bool FRenderMasterTransformAssistant::IsPlanning() const
{
    return State == ERenderMasterTransformAssistantState::Planning;
}

FText FRenderMasterTransformAssistant::GetStateText() const
{
    switch (State)
    {
        case ERenderMasterTransformAssistantState::Planning: return NSLOCTEXT("RenderMasterBot", "TransformPlanning", "Planning");
        case ERenderMasterTransformAssistantState::Proposed: return NSLOCTEXT("RenderMasterBot", "TransformProposed", "Approval required");
        case ERenderMasterTransformAssistantState::Unresolved: return NSLOCTEXT("RenderMasterBot", "TransformUnresolved", "Unresolved");
        case ERenderMasterTransformAssistantState::Failed: return NSLOCTEXT("RenderMasterBot", "TransformFailed", "Failed");
        case ERenderMasterTransformAssistantState::Applied: return NSLOCTEXT("RenderMasterBot", "TransformApplied", "Applied");
        case ERenderMasterTransformAssistantState::Rejected: return NSLOCTEXT("RenderMasterBot", "TransformRejected", "Rejected");
        default: return NSLOCTEXT("RenderMasterBot", "TransformReady", "Ready");
    }
}

FText FRenderMasterTransformAssistant::GetSummaryText() const
{
    if (State == ERenderMasterTransformAssistantState::Planning)
    {
        return NSLOCTEXT("RenderMasterBot", "TransformPlanningSummary", "Interpreting the request as a bounded world-space Transform edit. The Actor is not being changed.");
    }
    if (State == ERenderMasterTransformAssistantState::Proposed)
    {
        return FText::FromString(FString::Printf(
            TEXT("Target Actor\n%s\n%s\n\nRequested changes\n%s\n\nBefore\n%s\n\nAfter\n%s\n\nWhy\n%s\n\nApproval changes only this Actor in the open level. The level is not saved automatically, and Ctrl+Z is supported."),
            *Proposal.ActorName,
            *Proposal.ActorPath,
            *Proposal.ChangeSummary,
            *SnapshotText(Proposal.Before),
            *SnapshotText(Proposal.After),
            *Proposal.Rationale));
    }
    if (State == ERenderMasterTransformAssistantState::Unresolved)
    {
        return FText::FromString(FString::Printf(
            TEXT("%s\n\nMissing capability\n%s"),
            *Proposal.Rationale,
            *Proposal.MissingCapabilities));
    }
    if (State == ERenderMasterTransformAssistantState::Failed) return FText::FromString(ErrorText);
    if (State == ERenderMasterTransformAssistantState::Applied)
    {
        return FText::FromString(FString::Printf(
            TEXT("Applied the approved world Transform to %s. The level was not saved automatically. Use Ctrl+Z to undo."),
            *Proposal.ActorName));
    }
    if (State == ERenderMasterTransformAssistantState::Rejected)
    {
        return NSLOCTEXT("RenderMasterBot", "TransformRejectedSummary", "The Transform proposal was rejected. No scene change was applied.");
    }
    return NSLOCTEXT("RenderMasterBot", "TransformReadySummary", "Select exactly one Actor and describe a world-space move, rotation, or scale change.");
}

FText FRenderMasterTransformAssistant::GetLogText() const
{
    return FText::FromString(ProcessLog);
}

FLinearColor FRenderMasterTransformAssistant::GetStateColor() const
{
    if (State == ERenderMasterTransformAssistantState::Proposed) return FLinearColor(0.10f, 0.50f, 0.82f);
    if (State == ERenderMasterTransformAssistantState::Applied) return FLinearColor(0.12f, 0.62f, 0.38f);
    if (State == ERenderMasterTransformAssistantState::Failed) return FLinearColor(0.9f, 0.2f, 0.2f);
    if (State == ERenderMasterTransformAssistantState::Unresolved) return FLinearColor(0.95f, 0.55f, 0.15f);
    return FLinearColor(0.2f, 0.23f, 0.28f);
}

bool FRenderMasterTransformAssistant::Tick(float DeltaTime)
{
    ReadProcessOutput();
    if (ProcessHandle.IsValid() && !FPlatformProcess::IsProcRunning(ProcessHandle)) FinishProcess();
    return true;
}

void FRenderMasterTransformAssistant::FinishProcess()
{
    ReadProcessOutput();
    int32 ExitCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ExitCode);
    CloseProcessResources();
    if (ExitCode != 0)
    {
        Fail(FString::Printf(TEXT("Transform assistant process exited with code %d.\n%s"), ExitCode, *ProcessLog));
        return;
    }
    FString Error;
    if (!FRenderMasterTransformProposal::LoadFromFile(ProposalOutputPath, Proposal, Error))
    {
        Fail(Error);
        return;
    }
    State = Proposal.Status == TEXT("proposed")
        ? ERenderMasterTransformAssistantState::Proposed
        : ERenderMasterTransformAssistantState::Unresolved;
}

void FRenderMasterTransformAssistant::ReadProcessOutput()
{
    if (StdOutRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdOutRead));
    if (StdErrRead != nullptr) AppendLog(FPlatformProcess::ReadPipe(StdErrRead));
}

void FRenderMasterTransformAssistant::CloseProcessResources()
{
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::CloseProc(ProcessHandle);
        ProcessHandle.Reset();
    }
    if (StdOutRead != nullptr || StdOutWrite != nullptr) FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    if (StdErrRead != nullptr || StdErrWrite != nullptr) FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = StdOutWrite = StdErrRead = StdErrWrite = nullptr;
}

void FRenderMasterTransformAssistant::AppendLog(const FString& Text)
{
    if (Text.IsEmpty()) return;
    ProcessLog += Text;
    if (!ProcessLog.EndsWith(TEXT("\n"))) ProcessLog += TEXT("\n");
    if (ProcessLog.Len() > 12000) ProcessLog.RightInline(12000, EAllowShrinking::No);
}

void FRenderMasterTransformAssistant::Fail(const FString& Error)
{
    ErrorText = Error;
    State = ERenderMasterTransformAssistantState::Failed;
    AppendLog(Error);
}
