#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

class FRenderMasterWorkflowController;

enum class ERenderMasterConversationState : uint8
{
    Ready,
    Thinking,
    Completed,
    Failed,
};

struct FRenderMasterConversationSelectionItem
{
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorKind;
};

struct FRenderMasterConversationContext
{
    FString ProjectName;
    FString LevelPath;
    FString PlayMode = TEXT("editor");
    TArray<FRenderMasterConversationSelectionItem> Selection;
    bool bSelectionTruncated = false;
    bool bLightingRigReviewReady = false;
    bool bShadowOptimizationReady = false;
    bool bShadowVerificationReady = false;
    FString PendingActionIntent;
};

struct FRenderMasterConversationMessage
{
    FString MessageId;
    FString Role;
    FString Content;
};

struct FRenderMasterConversationEvidenceReference
{
    FString EvidenceId;
    FString Kind;
    FString Path;
    FString Sha256;
};

struct FRenderMasterConversationResultEvent
{
    FString EventId;
    FString ConversationId;
    FString SourceTurnId;
    FString Intent;
    FString Phase;
    FString Status;
    FString Summary;
    FString ProposalId;
    FString ReportId;
    TArray<FString> SubjectIds;
    TArray<FRenderMasterConversationEvidenceReference> Evidence;
    FString CreatedAtUtc;
};

struct FRenderMasterConversationTaskState
{
    FString SourceTurnId;
    FString Intent;
    FString Status;
    FString ProposalId;
    FRenderMasterConversationEvidenceReference ProposalEvidence;
};

bool RenderMasterWriteConversationRequestFile(
    const FString& Filename,
    const FString& ConversationId,
    const FString& TurnId,
    const FString& Request,
    const FRenderMasterConversationContext& Context,
    const TArray<FRenderMasterConversationMessage>& History,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents,
    FString& OutError);

bool RenderMasterWriteConversationSessionFile(
    const FString& Filename,
    const FString& ConversationId,
    const FString& ProjectName,
    const TArray<FRenderMasterConversationMessage>& History,
    const TArray<FRenderMasterConversationResultEvent>& ResultEvents,
    const TOptional<FRenderMasterConversationTaskState>& ActiveTask,
    FString& OutError);

bool RenderMasterLoadConversationSessionFile(
    const FString& Filename,
    const FString& ExpectedProjectName,
    FString& OutConversationId,
    TArray<FRenderMasterConversationMessage>& OutHistory,
    TArray<FRenderMasterConversationResultEvent>& OutResultEvents,
    TOptional<FRenderMasterConversationTaskState>& OutActiveTask,
    FString& OutError);

struct FRenderMasterConversationTurn
{
    FString ConversationId;
    FString TurnId;
    FString Request;
    FString Outcome;
    FString Intent;
    FString AssistantMessage;
    FString ActionPrompt;
    FString ClarificationQuestion;
    TArray<FString> RequiredContext;
    FString ContextSha256;
    FString Model;
    int32 AttemptCount = 0;
};

bool RenderMasterParseConversationTurnFile(
    const FString& Filename,
    FRenderMasterConversationTurn& OutTurn,
    FString& OutError);

DECLARE_DELEGATE_OneParam(
    FOnRenderMasterConversationTurnCompleted,
    const FRenderMasterConversationTurn&);
DECLARE_DELEGATE_OneParam(
    FOnRenderMasterConversationTurnFailed,
    const FString&);

class FRenderMasterConversationAssistant
    : public TSharedFromThis<FRenderMasterConversationAssistant>
{
public:
    explicit FRenderMasterConversationAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterConversationAssistant();

    void Initialize();
    void Shutdown();
    bool StartTurn(
        const FString& Request,
        const FRenderMasterConversationContext& Context,
        const TArray<FRenderMasterConversationMessage>& History,
        const TArray<FRenderMasterConversationResultEvent>& ResultEvents);
    void Cancel();
    void ResetConversation();
    bool RestoreConversationId(const FString& InConversationId);

    bool CanStart() const;
    bool IsThinking() const { return State == ERenderMasterConversationState::Thinking; }
    ERenderMasterConversationState GetState() const { return State; }
    const FString& GetConversationId() const { return ConversationId; }
    FText GetStateText() const;

    void SetOnCompleted(FOnRenderMasterConversationTurnCompleted InDelegate)
    {
        OnCompleted = MoveTemp(InDelegate);
    }
    void SetOnFailed(FOnRenderMasterConversationTurnFailed InDelegate)
    {
        OnFailed = MoveTemp(InDelegate);
    }

private:
    bool Tick(float DeltaTime);
    bool WriteRequest(
        const FString& Filename,
        const FString& TurnId,
        const FString& Request,
        const FRenderMasterConversationContext& Context,
        const TArray<FRenderMasterConversationMessage>& History,
        const TArray<FRenderMasterConversationResultEvent>& ResultEvents,
        FString& OutError) const;
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterConversationState State = ERenderMasterConversationState::Ready;
    FString ConversationId;
    FString ActiveTurnId;
    FString ActiveRequest;
    FString TurnOutputPath;
    FString ErrorText;
    FString ProcessLog;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
    FOnRenderMasterConversationTurnCompleted OnCompleted;
    FOnRenderMasterConversationTurnFailed OnFailed;
};
