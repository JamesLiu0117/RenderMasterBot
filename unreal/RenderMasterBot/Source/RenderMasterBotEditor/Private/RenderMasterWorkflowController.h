#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "HAL/PlatformProcess.h"
#include "RenderMasterManifest.h"
#include "Templates/SharedPointer.h"
#include "Tickable.h"

struct FSlateBrush;
struct FSlateDynamicImageBrush;

class FRenderMasterWorkflowController : public TSharedFromThis<FRenderMasterWorkflowController>
{
public:
    FRenderMasterWorkflowController();
    ~FRenderMasterWorkflowController();

    void Initialize();
    void Shutdown();

    bool Start(const FString& Prompt, int32 MaxIterations);
    void Cancel();
    void OpenWorkflowFolder() const;
    void OpenPreview() const;

    bool IsRunning();
    bool CanStart() const;
    bool HasWorkflowFolder() const;
    bool HasPreview() const;

    const FString& GetPythonExecutable() const { return PythonExecutable; }
    const FString& GetAssetCatalog() const { return AssetCatalog; }
    const FString& GetWorkflowRoot() const { return WorkflowRoot; }
    const FString& GetProjectFile() const { return ProjectFile; }
    const FString& GetEngineRoot() const { return EngineRoot; }
    void SetPythonExecutable(const FString& Value);
    void SetAssetCatalog(const FString& Value);
    void SetWorkflowRoot(const FString& Value);

    FText GetStatusText() const;
    FText GetStageText() const;
    FText GetDetailText() const;
    FText GetEvaluationText() const;
    FText GetStatisticsText() const;
    FText GetLogText() const;
    float GetProgress() const;
    FLinearColor GetStatusColor() const;
    FLinearColor GetStageColor(const FString& StageName) const;
    const FSlateBrush* GetPreviewBrush() const;

private:
    bool Tick(float DeltaTime);
    bool ValidateConfiguration(FString& OutError) const;
    void LoadConfiguration();
    void SaveConfiguration() const;
    void PollManifest();
    void RefreshArtifacts();
    void ReadProcessOutput();
    void FinishProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Text);

    FString PythonExecutable;
    FString AssetCatalog;
    FString WorkflowRoot;
    FString ProjectFile;
    FString EngineRoot;
    FString ActiveWorkflowDirectory;
    FString PreviewPath;
    FString LogText;
    FString LastActiveStage = TEXT("idle");

    FRenderMasterManifestSnapshot Snapshot;
    FRenderMasterEvaluation Evaluation;
    FRenderMasterImageStatistics Statistics;
    TSharedPtr<FSlateDynamicImageBrush> PreviewBrush;

    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
    double LastPollSeconds = 0.0;
    FDateTime LastPreviewTimestamp;
    bool bCancelRequested = false;
};
