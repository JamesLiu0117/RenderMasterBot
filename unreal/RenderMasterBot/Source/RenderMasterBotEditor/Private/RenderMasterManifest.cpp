#include "RenderMasterManifest.h"

#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
bool LoadJsonObject(const FString& Filename, TSharedPtr<FJsonObject>& OutObject, FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read %s"), *Filename);
        return false;
    }

    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, OutObject) || !OutObject.IsValid())
    {
        OutError = FString::Printf(TEXT("Invalid JSON in %s"), *Filename);
        return false;
    }
    return true;
}
}

bool FRenderMasterManifestSnapshot::IsTerminal() const
{
    return Status == TEXT("succeeded") || Status == TEXT("failed") || Status == TEXT("stopped") || Status == TEXT("cancelled");
}

bool FRenderMasterManifestSnapshot::Parse(const FString& JsonText, FRenderMasterManifestSnapshot& OutSnapshot, FString& OutError)
{
    TSharedPtr<FJsonObject> Object;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid())
    {
        OutError = TEXT("Invalid workflow manifest JSON");
        return false;
    }

    FRenderMasterManifestSnapshot Candidate;
    if (!Object->TryGetStringField(TEXT("workflow_id"), Candidate.WorkflowId)
        || !Object->TryGetStringField(TEXT("status"), Candidate.Status)
        || !Object->TryGetStringField(TEXT("stage"), Candidate.Stage))
    {
        OutError = TEXT("Manifest is missing workflow_id, status, or stage");
        return false;
    }

    Object->TryGetStringField(TEXT("stop_reason"), Candidate.StopReason);
    Object->TryGetNumberField(TEXT("max_iterations"), Candidate.MaxIterations);

    const TArray<TSharedPtr<FJsonValue>>* Iterations = nullptr;
    if (Object->TryGetArrayField(TEXT("iterations"), Iterations) && Iterations != nullptr)
    {
        Candidate.IterationCount = Iterations->Num();
    }

    const TArray<TSharedPtr<FJsonValue>>* Errors = nullptr;
    if (Object->TryGetArrayField(TEXT("errors"), Errors) && Errors != nullptr && !Errors->IsEmpty())
    {
        Candidate.Error = (*Errors)[0]->AsString();
    }

    OutSnapshot = MoveTemp(Candidate);
    return true;
}

bool FRenderMasterManifestSnapshot::LoadFromFile(const FString& Filename, FRenderMasterManifestSnapshot& OutSnapshot, FString& OutError)
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *Filename))
    {
        OutError = FString::Printf(TEXT("Could not read %s"), *Filename);
        return false;
    }
    return Parse(JsonText, OutSnapshot, OutError);
}

bool LoadRenderMasterEvaluation(const FString& Filename, FRenderMasterEvaluation& OutEvaluation, FString& OutError)
{
    TSharedPtr<FJsonObject> Object;
    if (!LoadJsonObject(Filename, Object, OutError))
    {
        return false;
    }

    Object->TryGetStringField(TEXT("verdict"), OutEvaluation.Verdict);
    Object->TryGetStringField(TEXT("summary"), OutEvaluation.Summary);
    return !OutEvaluation.Verdict.IsEmpty() || !OutEvaluation.Summary.IsEmpty();
}

bool LoadRenderMasterImageStatistics(const FString& Filename, FRenderMasterImageStatistics& OutStatistics, FString& OutError)
{
    TSharedPtr<FJsonObject> Object;
    if (!LoadJsonObject(Filename, Object, OutError))
    {
        return false;
    }

    Object->TryGetNumberField(TEXT("width_px"), OutStatistics.Width);
    Object->TryGetNumberField(TEXT("height_px"), OutStatistics.Height);
    Object->TryGetNumberField(TEXT("mean_luminance"), OutStatistics.MeanLuminance);
    Object->TryGetNumberField(TEXT("dark_pixel_fraction"), OutStatistics.DarkPixelFraction);
    Object->TryGetNumberField(TEXT("clipped_pixel_fraction"), OutStatistics.ClippedPixelFraction);
    Object->TryGetNumberField(TEXT("foreground_fraction"), OutStatistics.ForegroundFraction);
    OutStatistics.bAvailable = OutStatistics.Width > 0 && OutStatistics.Height > 0;
    return OutStatistics.bAvailable;
}
