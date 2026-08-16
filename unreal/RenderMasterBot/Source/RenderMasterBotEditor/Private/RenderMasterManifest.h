#pragma once

#include "CoreMinimal.h"

struct FRenderMasterEvaluation
{
    FString Verdict;
    FString Summary;
};

struct FRenderMasterImageStatistics
{
    bool bAvailable = false;
    int32 Width = 0;
    int32 Height = 0;
    double MeanLuminance = 0.0;
    double DarkPixelFraction = 0.0;
    double ClippedPixelFraction = 0.0;
    double ForegroundFraction = 0.0;
};

struct FRenderMasterManifestSnapshot
{
    FString WorkflowId;
    FString Status = TEXT("ready");
    FString Stage = TEXT("idle");
    FString StopReason;
    FString Error;
    int32 IterationCount = 0;
    int32 MaxIterations = 1;

    bool IsTerminal() const;
    static bool Parse(const FString& JsonText, FRenderMasterManifestSnapshot& OutSnapshot, FString& OutError);
    static bool LoadFromFile(const FString& Filename, FRenderMasterManifestSnapshot& OutSnapshot, FString& OutError);
};

bool LoadRenderMasterEvaluation(const FString& Filename, FRenderMasterEvaluation& OutEvaluation, FString& OutError);
bool LoadRenderMasterImageStatistics(const FString& Filename, FRenderMasterImageStatistics& OutStatistics, FString& OutError);
