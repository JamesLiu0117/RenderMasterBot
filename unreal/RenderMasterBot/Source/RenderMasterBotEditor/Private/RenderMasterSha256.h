#pragma once

#include "CoreMinimal.h"

bool RenderMasterSha256Bytes(
    TConstArrayView<uint8> Bytes,
    FString& OutSha256);

bool RenderMasterSha256File(
    const FString& Filename,
    FString& OutSha256,
    int64* OutSizeBytes = nullptr);
