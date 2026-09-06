#include "RenderMasterConversationAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
FString ConversationIdentifier(const TCHAR* Prefix)
{
    return FString::Printf(
        TEXT("%s_%s"),
        Prefix,
        *FGuid::NewGuid().ToString(EGuidFormats::Digits).ToLower());
}

FString QuoteConversationArgument(const FString& Value)
{
    return FString::Printf(
        TEXT("\"%s\""),
        *Value.Replace(TEXT("\""), TEXT("\\\"")));
}

bool IsSha256(const FString& Value)
{
    if (Value.Len() != 64) return false;
    for (const TCHAR Character : Value)
    {
        if (!FChar::IsHexDigit(Character) || FChar::IsUpper(Character))
            return false;
    }
    return true;
}

bool IsContractIdentifier(const FString& Value)
{
    if (Value.IsEmpty() || Value.Len() > 64 || !FChar::IsLower(Value[0]))
        return false;
    for (const TCHAR Character : Value)
    {
        if (!FChar::IsLower(Character)
            && !FChar::IsDigit(Character)
            && Character != TEXT('_')
            && Character != TEXT('-'))
        {
            return false;
        }
    }
    return true;
}

bool ReadRequiredString(
    const TSharedPtr<FJsonObject>& Json,
    const TCHAR* Field,
    FString& Out,
    FString& OutError)
{
    if (!Json.IsValid() || !Json->TryGetStringField(Field, Out)
        || Out.TrimStartAndEnd().IsEmpty())
    {
        OutError = FString::Printf(
            TEXT("Conversation turn is missing non-empty field '%s'."),
            Field);
        return false;
    }
    return true;
}

bool ReadOptionalString(
    const TSharedPtr<FJsonObject>& Json,
    const TCHAR* Field,
    FString& Out,
    FString& OutError)
{
    Out.Reset();
    if (!Json.IsValid() || !Json->HasField(Field)
        || Json->HasTypedField<EJson::Null>(Field))
    {
        return true;
    }
    if (!Json->TryGetStringField(Field, Out) || Out.TrimStartAndEnd().IsEmpty())
    {
        OutError = FString::Printf(
            TEXT("Conversation turn field '%s' must be null or non-empty text."),
            Field);
        return false;
    }
    return true;
}

bool IsRegisteredActionIntent(const FString& Intent)
{
    static const TSet<FString> Intents = {
        TEXT("material_project"),
        TEXT("material_external"),
        TEXT("transform"),
        TEXT("light"),
        TEXT("lighting_rig"),
        TEXT("lighting_rig_review"),
        TEXT("camera"),
        TEXT("performance_selection"),
        TEXT("level_audit"),
        TEXT("runtime_performance"),
        TEXT("gpu_scope"),
        TEXT("actor_gpu_impact"),
        TEXT("level_profiling"),
        TEXT("shadow_optimization"),
        TEXT("shadow_verification"),
        TEXT("render_evaluate"),
    };
    return Intents.Contains(Intent);
}

bool IsRegisteredRequiredContext(const FString& Value)
{
    static const TSet<FString> Values = {
        TEXT("actor_selection"),
        TEXT("static_mesh_selection"),
        TEXT("light_selection"),
        TEXT("camera_selection"),
        TEXT("lighting_rig_selection"),
        TEXT("active_pie_or_sie"),
        TEXT("applied_lighting_rig"),
        TEXT("completed_level_profile"),
        TEXT("applied_shadow_optimization"),
        TEXT("pending_action_decision"),
        TEXT("more_specific_request"),
    };
    return Values.Contains(Value);
}

bool IsRegisteredEvidenceKind(const FString& Value)
{
    return TSet<FString>({
        TEXT("proposal"),
        TEXT("report"),
        TEXT("capture"),
        TEXT("render"),
        TEXT("trace"),
        TEXT("log"),
        TEXT("other"),
    }).Contains(Value);
}

bool ToolResultPhaseMatchesStatus(
    const FString& Phase,
    const FString& Status)
{
    if (Phase == TEXT("proposal"))
        return TSet<FString>({
            TEXT("needs_review"), TEXT("unresolved"), TEXT("failed")
        }).Contains(Status);
    if (Phase == TEXT("analysis") || Phase == TEXT("verification"))
        return TSet<FString>({
            TEXT("succeeded"), TEXT("unresolved"), TEXT("failed")
        }).Contains(Status);
    if (Phase == TEXT("execution"))
        return TSet<FString>({TEXT("applied"), TEXT("failed")}).Contains(Status);
    if (Phase == TEXT("rejection")) return Status == TEXT("rejected");
    if (Phase == TEXT("failure")) return Status == TEXT("failed");
    if (Phase == TEXT("cancellation")) return Status == TEXT("cancelled");
    return false;
}

bool IsValidToolResultEvent(
    const FRenderMasterConversationResultEvent& Event,
    const FString& ConversationId)
{
    if (!IsContractIdentifier(Event.EventId)
        || Event.ConversationId != ConversationId
        || !IsContractIdentifier(Event.ConversationId)
        || (!Event.SourceTurnId.IsEmpty()
            && !IsContractIdentifier(Event.SourceTurnId))
        || !IsRegisteredActionIntent(Event.Intent)
        || !ToolResultPhaseMatchesStatus(Event.Phase, Event.Status)
        || Event.Summary.IsEmpty()
        || Event.Summary.Len() > 4000
        || Event.SubjectIds.Num() > 32
        || Event.Evidence.Num() > 16
        || Event.CreatedAtUtc.IsEmpty())
    {
        return false;
    }
    if (TSet<FString>({
            TEXT("needs_review"), TEXT("applied"), TEXT("rejected")
        }).Contains(Event.Status)
        && Event.ProposalId.IsEmpty())
    {
        return false;
    }
    FDateTime CreatedAt;
    if (!FDateTime::ParseIso8601(*Event.CreatedAtUtc, CreatedAt)
        || !Event.CreatedAtUtc.EndsWith(TEXT("Z")))
    {
        return false;
    }
    TSet<FString> SeenSubjects;
    for (const FString& SubjectId : Event.SubjectIds)
    {
        if (SubjectId.IsEmpty() || SeenSubjects.Contains(SubjectId)) return false;
        SeenSubjects.Add(SubjectId);
    }
    TSet<FString> SeenEvidenceIds;
    TSet<FString> SeenEvidencePaths;
    for (const FRenderMasterConversationEvidenceReference& Evidence : Event.Evidence)
    {
        if (Evidence.EvidenceId.IsEmpty()
            || SeenEvidenceIds.Contains(Evidence.EvidenceId)
            || !IsRegisteredEvidenceKind(Evidence.Kind)
            || Evidence.Path.IsEmpty()
            || SeenEvidencePaths.Contains(Evidence.Path)
            || !IsSha256(Evidence.Sha256))
        {
            return false;
        }
        SeenEvidenceIds.Add(Evidence.EvidenceId);
        SeenEvidencePaths.Add(Evidence.Path);
    }
    return true;
}

bool IsValidConversationTask(
    const FRenderMasterConversationTaskState& Task,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents)
{
    if (!IsContractIdentifier(Task.SourceTurnId)
        || !IsRegisteredActionIntent(Task.Intent)
        || (Task.Status != TEXT("planning")
            && Task.Status != TEXT("needs_review")))
    {
        return false;
    }
    if (Task.Status == TEXT("planning"))
    {
        return Task.ProposalId.IsEmpty()
            && Task.ProposalEvidence.EvidenceId.IsEmpty()
            && Task.ProposalEvidence.Kind.IsEmpty()
            && Task.ProposalEvidence.Path.IsEmpty()
            && Task.ProposalEvidence.Sha256.IsEmpty();
    }
    if (!IsContractIdentifier(Task.ProposalId)
        || !IsContractIdentifier(Task.ProposalEvidence.EvidenceId)
        || Task.ProposalEvidence.Kind != TEXT("proposal")
        || Task.ProposalEvidence.Path.IsEmpty()
        || !IsSha256(Task.ProposalEvidence.Sha256))
    {
        return false;
    }
    for (int32 Index = ResultEvents.Num() - 1; Index >= 0; --Index)
    {
        const FRenderMasterConversationResultEvent& Event = ResultEvents[Index];
        if (Event.SourceTurnId != Task.SourceTurnId
            || Event.Intent != Task.Intent
            || Event.Phase != TEXT("proposal")
            || Event.Status != TEXT("needs_review")
            || Event.ProposalId != Task.ProposalId)
        {
            continue;
        }
        for (const FRenderMasterConversationEvidenceReference& Evidence : Event.Evidence)
        {
            if (Evidence.EvidenceId == Task.ProposalEvidence.EvidenceId
                && Evidence.Kind == Task.ProposalEvidence.Kind
                && Evidence.Path == Task.ProposalEvidence.Path
                && Evidence.Sha256 == Task.ProposalEvidence.Sha256)
            {
                return true;
            }
        }
        return false;
    }
    return false;
}

TSharedRef<FJsonObject> SelectionJson(
    const FRenderMasterConversationSelectionItem& Item)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("actor_name"), Item.ActorName);
    Json->SetStringField(TEXT("actor_path"), Item.ActorPath);
    Json->SetStringField(TEXT("actor_class"), Item.ActorClass);
    Json->SetStringField(TEXT("actor_kind"), Item.ActorKind);
    return Json;
}

TSharedRef<FJsonObject> ContextJson(
    const FRenderMasterConversationContext& Context)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Json->SetStringField(TEXT("project_name"), Context.ProjectName);
    Json->SetStringField(TEXT("level_path"), Context.LevelPath);
    Json->SetStringField(TEXT("play_mode"), Context.PlayMode);
    TArray<TSharedPtr<FJsonValue>> Selection;
    for (const FRenderMasterConversationSelectionItem& Item : Context.Selection)
    {
        Selection.Add(MakeShared<FJsonValueObject>(SelectionJson(Item)));
    }
    Json->SetArrayField(TEXT("selection"), Selection);
    Json->SetBoolField(TEXT("selection_truncated"), Context.bSelectionTruncated);
    Json->SetBoolField(
        TEXT("lighting_rig_review_ready"),
        Context.bLightingRigReviewReady);
    Json->SetBoolField(
        TEXT("shadow_optimization_ready"),
        Context.bShadowOptimizationReady);
    Json->SetBoolField(
        TEXT("shadow_verification_ready"),
        Context.bShadowVerificationReady);
    if (Context.PendingActionIntent.IsEmpty())
    {
        Json->SetField(
            TEXT("pending_action_intent"),
            MakeShared<FJsonValueNull>());
    }
    else
    {
        Json->SetStringField(
            TEXT("pending_action_intent"),
            Context.PendingActionIntent);
    }
    return Json;
}

TSharedRef<FJsonObject> EvidenceJson(
    const FRenderMasterConversationEvidenceReference& Evidence)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("evidence_id"), Evidence.EvidenceId);
    Json->SetStringField(TEXT("kind"), Evidence.Kind);
    Json->SetStringField(TEXT("path"), Evidence.Path);
    Json->SetStringField(TEXT("sha256"), Evidence.Sha256);
    return Json;
}

TSharedRef<FJsonObject> MessageJson(
    const FRenderMasterConversationMessage& Message)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("message_id"), Message.MessageId);
    Json->SetStringField(TEXT("role"), Message.Role);
    Json->SetStringField(TEXT("content"), Message.Content);
    return Json;
}

TSharedRef<FJsonObject> ResultEventJson(
    const FRenderMasterConversationResultEvent& Event)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Json->SetStringField(TEXT("event_id"), Event.EventId);
    Json->SetStringField(TEXT("conversation_id"), Event.ConversationId);
    if (Event.SourceTurnId.IsEmpty())
        Json->SetField(TEXT("source_turn_id"), MakeShared<FJsonValueNull>());
    else
        Json->SetStringField(TEXT("source_turn_id"), Event.SourceTurnId);
    Json->SetStringField(TEXT("intent"), Event.Intent);
    Json->SetStringField(TEXT("phase"), Event.Phase);
    Json->SetStringField(TEXT("status"), Event.Status);
    Json->SetStringField(TEXT("summary"), Event.Summary);
    if (Event.ProposalId.IsEmpty())
        Json->SetField(TEXT("proposal_id"), MakeShared<FJsonValueNull>());
    else
        Json->SetStringField(TEXT("proposal_id"), Event.ProposalId);
    if (Event.ReportId.IsEmpty())
        Json->SetField(TEXT("report_id"), MakeShared<FJsonValueNull>());
    else
        Json->SetStringField(TEXT("report_id"), Event.ReportId);

    TArray<TSharedPtr<FJsonValue>> SubjectIds;
    for (const FString& SubjectId : Event.SubjectIds)
        SubjectIds.Add(MakeShared<FJsonValueString>(SubjectId));
    Json->SetArrayField(TEXT("subject_ids"), SubjectIds);

    TArray<TSharedPtr<FJsonValue>> EvidenceValues;
    for (const FRenderMasterConversationEvidenceReference& Evidence : Event.Evidence)
        EvidenceValues.Add(MakeShared<FJsonValueObject>(EvidenceJson(Evidence)));
    Json->SetArrayField(TEXT("evidence"), EvidenceValues);
    Json->SetStringField(TEXT("created_at_utc"), Event.CreatedAtUtc);
    return Json;
}

TSharedRef<FJsonObject> TaskJson(
    const FRenderMasterConversationTaskState& Task)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("source_turn_id"), Task.SourceTurnId);
    Json->SetStringField(TEXT("intent"), Task.Intent);
    Json->SetStringField(TEXT("status"), Task.Status);
    if (Task.ProposalId.IsEmpty())
        Json->SetField(TEXT("proposal_id"), MakeShared<FJsonValueNull>());
    else
        Json->SetStringField(TEXT("proposal_id"), Task.ProposalId);
    if (Task.ProposalEvidence.EvidenceId.IsEmpty())
        Json->SetField(TEXT("proposal_evidence"), MakeShared<FJsonValueNull>());
    else
        Json->SetObjectField(
            TEXT("proposal_evidence"),
            EvidenceJson(Task.ProposalEvidence));
    return Json;
}

bool ParseEvidenceJson(
    const TSharedPtr<FJsonObject>& Json,
    FRenderMasterConversationEvidenceReference& OutEvidence,
    FString& OutError)
{
    return ReadRequiredString(
            Json, TEXT("evidence_id"), OutEvidence.EvidenceId, OutError)
        && ReadRequiredString(Json, TEXT("kind"), OutEvidence.Kind, OutError)
        && ReadRequiredString(Json, TEXT("path"), OutEvidence.Path, OutError)
        && ReadRequiredString(Json, TEXT("sha256"), OutEvidence.Sha256, OutError);
}

bool ParseResultEventJson(
    const TSharedPtr<FJsonObject>& Json,
    const FString& ConversationId,
    FRenderMasterConversationResultEvent& OutEvent,
    FString& OutError)
{
    FString SchemaVersion;
    if (!ReadRequiredString(Json, TEXT("schema_version"), SchemaVersion, OutError)
        || SchemaVersion != TEXT("0.1")
        || !ReadRequiredString(Json, TEXT("event_id"), OutEvent.EventId, OutError)
        || !ReadRequiredString(
            Json, TEXT("conversation_id"), OutEvent.ConversationId, OutError)
        || !ReadOptionalString(
            Json, TEXT("source_turn_id"), OutEvent.SourceTurnId, OutError)
        || !ReadRequiredString(Json, TEXT("intent"), OutEvent.Intent, OutError)
        || !ReadRequiredString(Json, TEXT("phase"), OutEvent.Phase, OutError)
        || !ReadRequiredString(Json, TEXT("status"), OutEvent.Status, OutError)
        || !ReadRequiredString(Json, TEXT("summary"), OutEvent.Summary, OutError)
        || !ReadOptionalString(
            Json, TEXT("proposal_id"), OutEvent.ProposalId, OutError)
        || !ReadOptionalString(Json, TEXT("report_id"), OutEvent.ReportId, OutError)
        || !ReadRequiredString(
            Json, TEXT("created_at_utc"), OutEvent.CreatedAtUtc, OutError))
    {
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* SubjectValues = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* EvidenceValues = nullptr;
    if (!Json->TryGetArrayField(TEXT("subject_ids"), SubjectValues)
        || SubjectValues == nullptr
        || !Json->TryGetArrayField(TEXT("evidence"), EvidenceValues)
        || EvidenceValues == nullptr)
    {
        OutError = TEXT("Conversation result arrays are invalid.");
        return false;
    }
    OutEvent.SubjectIds.Reset();
    for (const TSharedPtr<FJsonValue>& Value : *SubjectValues)
    {
        FString SubjectId;
        if (!Value.IsValid() || !Value->TryGetString(SubjectId))
        {
            OutError = TEXT("Conversation result subject identity is invalid.");
            return false;
        }
        OutEvent.SubjectIds.Add(MoveTemp(SubjectId));
    }
    OutEvent.Evidence.Reset();
    for (const TSharedPtr<FJsonValue>& Value : *EvidenceValues)
    {
        const TSharedPtr<FJsonObject>* EvidenceObject = nullptr;
        FRenderMasterConversationEvidenceReference Evidence;
        if (!Value.IsValid()
            || !Value->TryGetObject(EvidenceObject)
            || EvidenceObject == nullptr
            || !EvidenceObject->IsValid()
            || !ParseEvidenceJson(*EvidenceObject, Evidence, OutError))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Conversation result evidence is invalid.");
            return false;
        }
        OutEvent.Evidence.Add(MoveTemp(Evidence));
    }
    if (!IsValidToolResultEvent(OutEvent, ConversationId))
    {
        OutError = TEXT("Conversation result event failed strict validation.");
        return false;
    }
    return true;
}
}

bool RenderMasterParseConversationTurnFile(
    const FString& Filename,
    FRenderMasterConversationTurn& OutTurn,
    FString& OutError)
{
    FString Text;
    if (!FFileHelper::LoadFileToString(Text, *Filename))
    {
        OutError = TEXT("Could not read the Assistant conversation turn.");
        return false;
    }
    TSharedPtr<FJsonObject> Json;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    if (!FJsonSerializer::Deserialize(Reader, Json) || !Json.IsValid())
    {
        OutError = TEXT("Assistant conversation output is not a JSON object.");
        return false;
    }

    FString SchemaVersion;
    if (!ReadRequiredString(
            Json, TEXT("schema_version"), SchemaVersion, OutError)
        || SchemaVersion != TEXT("0.1")
        || !ReadRequiredString(
            Json, TEXT("conversation_id"), OutTurn.ConversationId, OutError)
        || !ReadRequiredString(
            Json, TEXT("turn_id"), OutTurn.TurnId, OutError)
        || !ReadRequiredString(Json, TEXT("request"), OutTurn.Request, OutError)
        || !ReadRequiredString(Json, TEXT("outcome"), OutTurn.Outcome, OutError)
        || !ReadRequiredString(Json, TEXT("intent"), OutTurn.Intent, OutError)
        || !ReadRequiredString(
            Json,
            TEXT("assistant_message"),
            OutTurn.AssistantMessage,
            OutError)
        || !ReadOptionalString(
            Json, TEXT("action_prompt"), OutTurn.ActionPrompt, OutError)
        || !ReadOptionalString(
            Json,
            TEXT("clarification_question"),
            OutTurn.ClarificationQuestion,
            OutError)
        || !ReadRequiredString(
            Json,
            TEXT("context_sha256"),
            OutTurn.ContextSha256,
            OutError)
        || !ReadRequiredString(Json, TEXT("model"), OutTurn.Model, OutError))
    {
        if (OutError.IsEmpty())
            OutError = TEXT("Unsupported Assistant conversation schema version.");
        return false;
    }
    if (SchemaVersion != TEXT("0.1"))
    {
        OutError = TEXT("Unsupported Assistant conversation schema version.");
        return false;
    }
    if (!IsSha256(OutTurn.ContextSha256))
    {
        OutError = TEXT("Assistant conversation context hash is invalid.");
        return false;
    }

    double AttemptCount = 0.0;
    if (!Json->TryGetNumberField(TEXT("attempt_count"), AttemptCount)
        || (AttemptCount != 1.0 && AttemptCount != 2.0))
    {
        OutError = TEXT("Assistant conversation attempt_count must be 1 or 2.");
        return false;
    }
    OutTurn.AttemptCount = static_cast<int32>(AttemptCount);

    const TArray<TSharedPtr<FJsonValue>>* RequiredContext = nullptr;
    if (!Json->TryGetArrayField(TEXT("required_context"), RequiredContext)
        || RequiredContext == nullptr || RequiredContext->Num() > 10)
    {
        OutError = TEXT("Assistant conversation required_context is invalid.");
        return false;
    }
    OutTurn.RequiredContext.Reset();
    TSet<FString> SeenContext;
    for (const TSharedPtr<FJsonValue>& Value : *RequiredContext)
    {
        FString Item;
        if (!Value.IsValid() || !Value->TryGetString(Item)
            || !IsRegisteredRequiredContext(Item)
            || SeenContext.Contains(Item))
        {
            OutError = TEXT(
                "Assistant conversation required_context contains an invalid value.");
            return false;
        }
        SeenContext.Add(Item);
        OutTurn.RequiredContext.Add(Item);
    }

    if (OutTurn.Outcome == TEXT("route"))
    {
        if (!IsRegisteredActionIntent(OutTurn.Intent)
            || OutTurn.ActionPrompt.IsEmpty()
            || !OutTurn.ClarificationQuestion.IsEmpty()
            || !OutTurn.RequiredContext.IsEmpty())
        {
            OutError = TEXT("Assistant route payload is inconsistent.");
            return false;
        }
    }
    else if (OutTurn.Outcome == TEXT("clarify"))
    {
        if ((OutTurn.Intent != TEXT("clarify")
                && !IsRegisteredActionIntent(OutTurn.Intent))
            || OutTurn.ClarificationQuestion.IsEmpty()
            || OutTurn.RequiredContext.IsEmpty()
            || !OutTurn.ActionPrompt.IsEmpty())
        {
            OutError = TEXT("Assistant clarification payload is inconsistent.");
            return false;
        }
    }
    else if (OutTurn.Outcome == TEXT("respond"))
    {
        if (!TSet<FString>({
                TEXT("answer_question"),
                TEXT("inspect_context"),
                TEXT("unsupported"),
            }).Contains(OutTurn.Intent)
            || !OutTurn.ActionPrompt.IsEmpty()
            || !OutTurn.ClarificationQuestion.IsEmpty()
            || !OutTurn.RequiredContext.IsEmpty())
        {
            OutError = TEXT("Assistant response payload is inconsistent.");
            return false;
        }
    }
    else
    {
        OutError = TEXT("Assistant conversation outcome is not registered.");
        return false;
    }
    return true;
}

FRenderMasterConversationAssistant::FRenderMasterConversationAssistant(
    TSharedPtr<FRenderMasterWorkflowController> InWorkflowController)
    : WorkflowController(MoveTemp(InWorkflowController)),
      ConversationId(ConversationIdentifier(TEXT("conversation")))
{
}

FRenderMasterConversationAssistant::~FRenderMasterConversationAssistant()
{
    Shutdown();
}

void FRenderMasterConversationAssistant::Initialize()
{
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateSP(
            AsShared(),
            &FRenderMasterConversationAssistant::Tick),
        0.1f);
}

void FRenderMasterConversationAssistant::Shutdown()
{
    if (TickHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        TickHandle.Reset();
    }
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    OnCompleted.Unbind();
    OnFailed.Unbind();
}

bool FRenderMasterConversationAssistant::StartTurn(
    const FString& Request,
    const FRenderMasterConversationContext& Context,
    const TArray<FRenderMasterConversationMessage>& History,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents)
{
    if (!CanStart()) return false;
    const FString CleanRequest = Request.TrimStartAndEnd();
    if (CleanRequest.IsEmpty())
    {
        Fail(TEXT("Enter a message before sending it to the Assistant."));
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
        Fail(TEXT(
            "Configure an existing Python executable and workflow root in Render & Evaluate."));
        return false;
    }
    if (Python.Contains(TEXT("\"")) || Root.Contains(TEXT("\"")))
    {
        Fail(TEXT("Assistant runtime paths cannot contain a double quote."));
        return false;
    }

    ActiveTurnId = ConversationIdentifier(TEXT("turn"));
    ActiveRequest = CleanRequest;
    const FString RequestDirectory = FPaths::Combine(
        Root,
        TEXT("assistant-conversations"),
        ConversationId,
        ActiveTurnId);
    IFileManager::Get().MakeDirectory(*RequestDirectory, true);
    const FString RequestPath = FPaths::Combine(
        RequestDirectory,
        TEXT("conversation_request.json"));
    TurnOutputPath = FPaths::Combine(
        RequestDirectory,
        TEXT("conversation_turn.json"));

    ErrorText.Reset();
    ProcessLog.Reset();
    FString Error;
    if (!WriteRequest(
            RequestPath,
            ActiveTurnId,
            CleanRequest,
            Context,
            History,
            ResultEvents,
            Error))
    {
        Fail(Error);
        return false;
    }

    const FString Arguments = FString::Printf(
        TEXT("-m render_master_bot assistant-conversation-turn --request %s --output %s"),
        *QuoteConversationArgument(RequestPath),
        *QuoteConversationArgument(TurnOutputPath));
    if (!FPlatformProcess::CreatePipe(StdOutRead, StdOutWrite)
        || !FPlatformProcess::CreatePipe(StdErrRead, StdErrWrite))
    {
        CloseProcessResources();
        Fail(TEXT("Could not create conversation process pipes."));
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
    State = ERenderMasterConversationState::Thinking;
    AppendLog(FString::Printf(
        TEXT("Routing local conversation turn %s (process %u)."),
        *ActiveTurnId,
        ProcessId));
    return true;
}

bool RenderMasterWriteConversationRequestFile(
    const FString& Filename,
    const FString& ConversationId,
    const FString& TurnId,
    const FString& Request,
    const FRenderMasterConversationContext& Context,
    const TArray<FRenderMasterConversationMessage>& History,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents,
    FString& OutError)
{
    if (!IsContractIdentifier(ConversationId)
        || !IsContractIdentifier(TurnId)
        || Request.IsEmpty()
        || Request.Len() > 8000
        || Context.ProjectName.IsEmpty() || Context.LevelPath.IsEmpty()
        || Context.Selection.Num() > 32
        || !TSet<FString>({TEXT("editor"), TEXT("pie"), TEXT("sie")})
                .Contains(Context.PlayMode))
    {
        OutError = TEXT("Current Unreal conversation context is invalid.");
        return false;
    }

    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Json->SetStringField(TEXT("conversation_id"), ConversationId);
    Json->SetStringField(TEXT("turn_id"), TurnId);
    Json->SetStringField(TEXT("request"), Request);
    Json->SetObjectField(TEXT("context"), ContextJson(Context));

    TArray<TSharedPtr<FJsonValue>> Messages;
    const int32 StartIndex = FMath::Max(0, History.Num() - 24);
    for (int32 Index = StartIndex; Index < History.Num(); ++Index)
    {
        const FRenderMasterConversationMessage& Message = History[Index];
        if ((Message.Role != TEXT("user") && Message.Role != TEXT("assistant"))
            || Message.MessageId.IsEmpty() || Message.Content.IsEmpty())
        {
            OutError = TEXT("Conversation history contains an invalid message.");
            return false;
        }
        Messages.Add(MakeShared<FJsonValueObject>(MessageJson(Message)));
    }
    Json->SetArrayField(TEXT("history"), Messages);

    TArray<TSharedPtr<FJsonValue>> Results;
    const int32 ResultStartIndex = FMath::Max(0, ResultEvents.Num() - 12);
    TSet<FString> SeenResultIds;
    for (int32 Index = ResultStartIndex; Index < ResultEvents.Num(); ++Index)
    {
        const FRenderMasterConversationResultEvent& Event = ResultEvents[Index];
        if (SeenResultIds.Contains(Event.EventId)
            || !IsValidToolResultEvent(Event, ConversationId))
        {
            OutError = TEXT("Conversation tool-result history contains an invalid event.");
            return false;
        }
        SeenResultIds.Add(Event.EventId);
        Results.Add(MakeShared<FJsonValueObject>(ResultEventJson(Event)));
    }
    Json->SetArrayField(TEXT("result_events"), Results);

    FString Text;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
    if (!FJsonSerializer::Serialize(Json, Writer)
        || !FFileHelper::SaveStringToFile(
            Text,
            *Filename,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        OutError = TEXT("Could not write the Assistant conversation request.");
        return false;
    }
    return true;
}

bool FRenderMasterConversationAssistant::WriteRequest(
    const FString& Filename,
    const FString& TurnId,
    const FString& Request,
    const FRenderMasterConversationContext& Context,
    const TArray<FRenderMasterConversationMessage>& History,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents,
    FString& OutError) const
{
    return RenderMasterWriteConversationRequestFile(
        Filename,
        ConversationId,
        TurnId,
        Request,
        Context,
        History,
        ResultEvents,
        OutError);
}

bool RenderMasterWriteConversationSessionFile(
    const FString& Filename,
    const FString& ConversationId,
    const FString& ProjectName,
    const TArray<FRenderMasterConversationMessage>& History,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents,
    const TOptional<FRenderMasterConversationTaskState>& ActiveTask,
    FString& OutError)
{
    if (Filename.IsEmpty()
        || !IsContractIdentifier(ConversationId)
        || ProjectName.TrimStartAndEnd().IsEmpty()
        || ProjectName.Len() > 240
        || History.Num() > 100
        || ResultEvents.Num() > 100)
    {
        OutError = TEXT("Conversation session identity or bounds are invalid.");
        return false;
    }

    TSet<FString> SeenMessageIds;
    TArray<TSharedPtr<FJsonValue>> Messages;
    for (const FRenderMasterConversationMessage& Message : History)
    {
        if (!IsContractIdentifier(Message.MessageId)
            || SeenMessageIds.Contains(Message.MessageId)
            || (Message.Role != TEXT("user") && Message.Role != TEXT("assistant"))
            || Message.Content.TrimStartAndEnd().IsEmpty()
            || Message.Content.Len() > 8000)
        {
            OutError = TEXT("Conversation session contains an invalid message.");
            return false;
        }
        SeenMessageIds.Add(Message.MessageId);
        Messages.Add(MakeShared<FJsonValueObject>(MessageJson(Message)));
    }

    TSet<FString> SeenResultIds;
    TArray<TSharedPtr<FJsonValue>> Results;
    FDateTime PreviousResultTime;
    bool bHasPreviousResultTime = false;
    for (const FRenderMasterConversationResultEvent& Event : ResultEvents)
    {
        FDateTime ResultTime;
        if (SeenResultIds.Contains(Event.EventId)
            || !IsValidToolResultEvent(Event, ConversationId)
            || !FDateTime::ParseIso8601(*Event.CreatedAtUtc, ResultTime)
            || (bHasPreviousResultTime && ResultTime < PreviousResultTime))
        {
            OutError = TEXT("Conversation session contains an invalid result event.");
            return false;
        }
        SeenResultIds.Add(Event.EventId);
        PreviousResultTime = ResultTime;
        bHasPreviousResultTime = true;
        Results.Add(MakeShared<FJsonValueObject>(ResultEventJson(Event)));
    }
    if (ActiveTask.IsSet()
        && !IsValidConversationTask(ActiveTask.GetValue(), ResultEvents))
    {
        OutError = TEXT("Conversation session active task is invalid.");
        return false;
    }

    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("schema_version"), TEXT("0.1"));
    Json->SetStringField(TEXT("conversation_id"), ConversationId);
    Json->SetStringField(TEXT("project_name"), ProjectName);
    Json->SetArrayField(TEXT("history"), Messages);
    Json->SetArrayField(TEXT("result_events"), Results);
    if (ActiveTask.IsSet())
        Json->SetObjectField(TEXT("active_task"), TaskJson(ActiveTask.GetValue()));
    else
        Json->SetField(TEXT("active_task"), MakeShared<FJsonValueNull>());
    Json->SetStringField(
        TEXT("updated_at_utc"),
        FDateTime::UtcNow().ToIso8601());

    FString Text;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
    if (!FJsonSerializer::Serialize(Json, Writer))
    {
        OutError = TEXT("Could not serialize the Assistant conversation session.");
        return false;
    }
    const FString Directory = FPaths::GetPath(Filename);
    if (!Directory.IsEmpty()) IFileManager::Get().MakeDirectory(*Directory, true);
    const FString TemporaryPath = Filename + TEXT(".tmp");
    if (!FFileHelper::SaveStringToFile(
            Text,
            *TemporaryPath,
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
        || !IFileManager::Get().Move(
            *Filename,
            *TemporaryPath,
            true,
            true,
            true,
            true))
    {
        IFileManager::Get().Delete(*TemporaryPath, false, true);
        OutError = TEXT("Could not atomically write the Assistant conversation session.");
        return false;
    }
    return true;
}

bool RenderMasterLoadConversationSessionFile(
    const FString& Filename,
    const FString& ExpectedProjectName,
    FString& OutConversationId,
    TArray<FRenderMasterConversationMessage>& OutHistory,
    TArray<FRenderMasterConversationResultEvent>& OutResultEvents,
    TOptional<FRenderMasterConversationTaskState>& OutActiveTask,
    FString& OutError)
{
    FString Text;
    if (!FFileHelper::LoadFileToString(Text, *Filename))
    {
        OutError = TEXT("Could not read the Assistant conversation session.");
        return false;
    }
    TSharedPtr<FJsonObject> Json;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    if (!FJsonSerializer::Deserialize(Reader, Json) || !Json.IsValid())
    {
        OutError = TEXT("Assistant conversation session is not a JSON object.");
        return false;
    }

    FString SchemaVersion;
    FString ConversationId;
    FString ProjectName;
    FString UpdatedAtUtc;
    if (!ReadRequiredString(Json, TEXT("schema_version"), SchemaVersion, OutError)
        || SchemaVersion != TEXT("0.1")
        || !ReadRequiredString(
            Json, TEXT("conversation_id"), ConversationId, OutError)
        || !ReadRequiredString(Json, TEXT("project_name"), ProjectName, OutError)
        || !ReadRequiredString(
            Json, TEXT("updated_at_utc"), UpdatedAtUtc, OutError)
        || !IsContractIdentifier(ConversationId)
        || ProjectName != ExpectedProjectName
        || !UpdatedAtUtc.EndsWith(TEXT("Z")))
    {
        if (OutError.IsEmpty())
            OutError = TEXT("Assistant conversation session identity is invalid.");
        return false;
    }
    FDateTime UpdatedAt;
    if (!FDateTime::ParseIso8601(*UpdatedAtUtc, UpdatedAt))
    {
        OutError = TEXT("Assistant conversation session timestamp is invalid.");
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* MessageValues = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* ResultValues = nullptr;
    if (!Json->TryGetArrayField(TEXT("history"), MessageValues)
        || MessageValues == nullptr
        || MessageValues->Num() > 100
        || !Json->TryGetArrayField(TEXT("result_events"), ResultValues)
        || ResultValues == nullptr
        || ResultValues->Num() > 100)
    {
        OutError = TEXT("Assistant conversation session arrays are invalid.");
        return false;
    }

    TArray<FRenderMasterConversationMessage> History;
    TSet<FString> SeenMessageIds;
    for (const TSharedPtr<FJsonValue>& Value : *MessageValues)
    {
        const TSharedPtr<FJsonObject>* MessageObject = nullptr;
        FRenderMasterConversationMessage Message;
        if (!Value.IsValid()
            || !Value->TryGetObject(MessageObject)
            || MessageObject == nullptr
            || !MessageObject->IsValid()
            || !ReadRequiredString(
                *MessageObject, TEXT("message_id"), Message.MessageId, OutError)
            || !ReadRequiredString(
                *MessageObject, TEXT("role"), Message.Role, OutError)
            || !ReadRequiredString(
                *MessageObject, TEXT("content"), Message.Content, OutError)
            || !IsContractIdentifier(Message.MessageId)
            || SeenMessageIds.Contains(Message.MessageId)
            || (Message.Role != TEXT("user") && Message.Role != TEXT("assistant"))
            || Message.Content.Len() > 8000)
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Assistant conversation session message is invalid.");
            return false;
        }
        SeenMessageIds.Add(Message.MessageId);
        History.Add(MoveTemp(Message));
    }

    TArray<FRenderMasterConversationResultEvent> ResultEvents;
    TSet<FString> SeenResultIds;
    FDateTime PreviousResultTime;
    bool bHasPreviousResultTime = false;
    for (const TSharedPtr<FJsonValue>& Value : *ResultValues)
    {
        const TSharedPtr<FJsonObject>* ResultObject = nullptr;
        FRenderMasterConversationResultEvent Event;
        if (!Value.IsValid()
            || !Value->TryGetObject(ResultObject)
            || ResultObject == nullptr
            || !ResultObject->IsValid()
            || !ParseResultEventJson(
                *ResultObject, ConversationId, Event, OutError)
            || SeenResultIds.Contains(Event.EventId))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Assistant conversation session result is invalid.");
            return false;
        }
        FDateTime ResultTime;
        if (!FDateTime::ParseIso8601(*Event.CreatedAtUtc, ResultTime)
            || (bHasPreviousResultTime && ResultTime < PreviousResultTime)
            || ResultTime > UpdatedAt)
        {
            OutError = TEXT("Assistant conversation session result order is invalid.");
            return false;
        }
        SeenResultIds.Add(Event.EventId);
        PreviousResultTime = ResultTime;
        bHasPreviousResultTime = true;
        ResultEvents.Add(MoveTemp(Event));
    }

    TOptional<FRenderMasterConversationTaskState> ActiveTask;
    if (!Json->HasField(TEXT("active_task")))
    {
        OutError = TEXT("Assistant conversation session active_task is missing.");
        return false;
    }
    if (!Json->HasTypedField<EJson::Null>(TEXT("active_task")))
    {
        const TSharedPtr<FJsonObject>* TaskObject = nullptr;
        FRenderMasterConversationTaskState Task;
        if (!Json->TryGetObjectField(TEXT("active_task"), TaskObject)
            || TaskObject == nullptr
            || !TaskObject->IsValid()
            || !ReadRequiredString(
                *TaskObject, TEXT("source_turn_id"), Task.SourceTurnId, OutError)
            || !ReadRequiredString(
                *TaskObject, TEXT("intent"), Task.Intent, OutError)
            || !ReadRequiredString(
                *TaskObject, TEXT("status"), Task.Status, OutError)
            || !ReadOptionalString(
                *TaskObject, TEXT("proposal_id"), Task.ProposalId, OutError))
        {
            if (OutError.IsEmpty())
                OutError = TEXT("Assistant conversation active task is invalid.");
            return false;
        }
        if (!(*TaskObject)->HasField(TEXT("proposal_evidence")))
        {
            OutError = TEXT("Assistant conversation task proposal_evidence is missing.");
            return false;
        }
        if (!(*TaskObject)->HasTypedField<EJson::Null>(TEXT("proposal_evidence")))
        {
            const TSharedPtr<FJsonObject>* EvidenceObject = nullptr;
            if (!(*TaskObject)->TryGetObjectField(
                    TEXT("proposal_evidence"), EvidenceObject)
                || EvidenceObject == nullptr
                || !EvidenceObject->IsValid()
                || !ParseEvidenceJson(
                    *EvidenceObject, Task.ProposalEvidence, OutError))
            {
                if (OutError.IsEmpty())
                    OutError = TEXT("Assistant conversation task evidence is invalid.");
                return false;
            }
        }
        if (!IsValidConversationTask(Task, ResultEvents))
        {
            OutError = TEXT("Assistant conversation active task failed validation.");
            return false;
        }
        ActiveTask = MoveTemp(Task);
    }

    OutConversationId = MoveTemp(ConversationId);
    OutHistory = MoveTemp(History);
    OutResultEvents = MoveTemp(ResultEvents);
    OutActiveTask = MoveTemp(ActiveTask);
    return true;
}

void FRenderMasterConversationAssistant::Cancel()
{
    if (ProcessHandle.IsValid())
        FPlatformProcess::TerminateProc(ProcessHandle, true);
    CloseProcessResources();
    State = ERenderMasterConversationState::Ready;
    ErrorText.Reset();
}

void FRenderMasterConversationAssistant::ResetConversation()
{
    if (IsThinking()) return;
    ConversationId = ConversationIdentifier(TEXT("conversation"));
    ActiveTurnId.Reset();
    ActiveRequest.Reset();
    TurnOutputPath.Reset();
    ErrorText.Reset();
    ProcessLog.Reset();
    State = ERenderMasterConversationState::Ready;
}

bool FRenderMasterConversationAssistant::RestoreConversationId(
    const FString& InConversationId)
{
    if (IsThinking() || !IsContractIdentifier(InConversationId)) return false;
    ConversationId = InConversationId;
    ActiveTurnId.Reset();
    ActiveRequest.Reset();
    TurnOutputPath.Reset();
    ErrorText.Reset();
    ProcessLog.Reset();
    State = ERenderMasterConversationState::Ready;
    return true;
}

bool FRenderMasterConversationAssistant::CanStart() const
{
    return State != ERenderMasterConversationState::Thinking;
}

FText FRenderMasterConversationAssistant::GetStateText() const
{
    if (State == ERenderMasterConversationState::Thinking)
        return FText::FromString(TEXT("Thinking with the local planner..."));
    if (State == ERenderMasterConversationState::Failed)
        return FText::FromString(ErrorText);
    return FText::FromString(TEXT("Ready for a message."));
}

bool FRenderMasterConversationAssistant::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (!ProcessHandle.IsValid()) return true;
    const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
    const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
    if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
    if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
    if (!FPlatformProcess::IsProcRunning(ProcessHandle)) CompleteProcess();
    return true;
}

void FRenderMasterConversationAssistant::CompleteProcess()
{
    int32 ReturnCode = -1;
    FPlatformProcess::GetProcReturnCode(ProcessHandle, &ReturnCode);
    const FString StdOut = FPlatformProcess::ReadPipe(StdOutRead);
    const FString StdErr = FPlatformProcess::ReadPipe(StdErrRead);
    if (!StdOut.IsEmpty()) AppendLog(StdOut.TrimStartAndEnd());
    if (!StdErr.IsEmpty()) AppendLog(StdErr.TrimStartAndEnd());
    CloseProcessResources();
    if (ReturnCode != 0)
    {
        Fail(ProcessLog.IsEmpty()
            ? FString::Printf(
                TEXT("Conversation process exited with code %d."),
                ReturnCode)
            : ProcessLog);
        return;
    }

    FRenderMasterConversationTurn Turn;
    FString Error;
    if (!RenderMasterParseConversationTurnFile(TurnOutputPath, Turn, Error))
    {
        Fail(Error);
        return;
    }
    if (Turn.ConversationId != ConversationId
        || Turn.TurnId != ActiveTurnId
        || Turn.Request != ActiveRequest)
    {
        Fail(TEXT(
            "Conversation output does not match the active session, turn, or request."));
        return;
    }
    State = ERenderMasterConversationState::Completed;
    OnCompleted.ExecuteIfBound(Turn);
}

void FRenderMasterConversationAssistant::CloseProcessResources()
{
    if (ProcessHandle.IsValid())
    {
        FPlatformProcess::CloseProc(ProcessHandle);
        ProcessHandle.Reset();
    }
    if (StdOutRead != nullptr || StdOutWrite != nullptr)
        FPlatformProcess::ClosePipe(StdOutRead, StdOutWrite);
    if (StdErrRead != nullptr || StdErrWrite != nullptr)
        FPlatformProcess::ClosePipe(StdErrRead, StdErrWrite);
    StdOutRead = nullptr;
    StdOutWrite = nullptr;
    StdErrRead = nullptr;
    StdErrWrite = nullptr;
}

void FRenderMasterConversationAssistant::AppendLog(const FString& Line)
{
    if (Line.IsEmpty()) return;
    if (!ProcessLog.IsEmpty()) ProcessLog += TEXT("\n");
    ProcessLog += Line;
    if (ProcessLog.Len() > 16000)
        ProcessLog = ProcessLog.Right(16000);
}

void FRenderMasterConversationAssistant::Fail(const FString& Error)
{
    ErrorText = Error.IsEmpty()
        ? TEXT("The local conversation router failed without an error message.")
        : Error;
    State = ERenderMasterConversationState::Failed;
    OnFailed.ExecuteIfBound(ErrorText);
}
