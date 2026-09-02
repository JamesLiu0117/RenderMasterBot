#include "RenderMasterConversationAssistant.h"

#include "RenderMasterWorkflowController.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
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
    const TArray<FRenderMasterConversationMessage>& History)
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

bool FRenderMasterConversationAssistant::WriteRequest(
    const FString& Filename,
    const FString& TurnId,
    const FString& Request,
    const FRenderMasterConversationContext& Context,
    const TArray<FRenderMasterConversationMessage>& History,
    FString& OutError) const
{
    if (Context.ProjectName.IsEmpty() || Context.LevelPath.IsEmpty()
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
        TSharedRef<FJsonObject> MessageJson = MakeShared<FJsonObject>();
        MessageJson->SetStringField(TEXT("message_id"), Message.MessageId);
        MessageJson->SetStringField(TEXT("role"), Message.Role);
        MessageJson->SetStringField(TEXT("content"), Message.Content);
        Messages.Add(MakeShared<FJsonValueObject>(MessageJson));
    }
    Json->SetArrayField(TEXT("history"), Messages);

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
