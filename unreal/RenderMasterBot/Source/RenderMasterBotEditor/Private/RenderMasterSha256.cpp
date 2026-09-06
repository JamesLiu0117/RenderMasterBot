#include "RenderMasterSha256.h"

#include "Misc/FileHelper.h"

namespace
{
constexpr uint32 RoundConstants[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
    0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
    0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
    0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
    0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
    0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
};

uint32 RotateRight(uint32 Value, uint32 Count)
{
    return (Value >> Count) | (Value << (32u - Count));
}
}

bool RenderMasterSha256Bytes(
    TConstArrayView<uint8> Bytes,
    FString& OutSha256)
{
    OutSha256.Reset();
    if (Bytes.Num() > MAX_int32 - 72) return false;

    TArray<uint8> Padded;
    Padded.Reserve(Bytes.Num() + 72);
    if (!Bytes.IsEmpty()) Padded.Append(Bytes.GetData(), Bytes.Num());
    Padded.Add(0x80u);
    while ((Padded.Num() % 64) != 56) Padded.Add(0u);
    const uint64 BitLength = static_cast<uint64>(Bytes.Num()) * 8u;
    for (int32 Shift = 56; Shift >= 0; Shift -= 8)
        Padded.Add(static_cast<uint8>((BitLength >> Shift) & 0xffu));

    uint32 Hash[8] = {
        0x6a09e667u,
        0xbb67ae85u,
        0x3c6ef372u,
        0xa54ff53au,
        0x510e527fu,
        0x9b05688cu,
        0x1f83d9abu,
        0x5be0cd19u,
    };

    for (int32 Offset = 0; Offset < Padded.Num(); Offset += 64)
    {
        uint32 Words[64] = {};
        for (int32 Index = 0; Index < 16; ++Index)
        {
            const int32 ByteOffset = Offset + Index * 4;
            Words[Index] =
                (static_cast<uint32>(Padded[ByteOffset]) << 24u)
                | (static_cast<uint32>(Padded[ByteOffset + 1]) << 16u)
                | (static_cast<uint32>(Padded[ByteOffset + 2]) << 8u)
                | static_cast<uint32>(Padded[ByteOffset + 3]);
        }
        for (int32 Index = 16; Index < 64; ++Index)
        {
            const uint32 S0 = RotateRight(Words[Index - 15], 7u)
                ^ RotateRight(Words[Index - 15], 18u)
                ^ (Words[Index - 15] >> 3u);
            const uint32 S1 = RotateRight(Words[Index - 2], 17u)
                ^ RotateRight(Words[Index - 2], 19u)
                ^ (Words[Index - 2] >> 10u);
            Words[Index] = Words[Index - 16] + S0 + Words[Index - 7] + S1;
        }

        uint32 A = Hash[0];
        uint32 B = Hash[1];
        uint32 C = Hash[2];
        uint32 D = Hash[3];
        uint32 E = Hash[4];
        uint32 F = Hash[5];
        uint32 G = Hash[6];
        uint32 H = Hash[7];
        for (int32 Index = 0; Index < 64; ++Index)
        {
            const uint32 Sum1 = RotateRight(E, 6u)
                ^ RotateRight(E, 11u)
                ^ RotateRight(E, 25u);
            const uint32 Choice = (E & F) ^ ((~E) & G);
            const uint32 Temporary1 =
                H + Sum1 + Choice + RoundConstants[Index] + Words[Index];
            const uint32 Sum0 = RotateRight(A, 2u)
                ^ RotateRight(A, 13u)
                ^ RotateRight(A, 22u);
            const uint32 Majority = (A & B) ^ (A & C) ^ (B & C);
            const uint32 Temporary2 = Sum0 + Majority;
            H = G;
            G = F;
            F = E;
            E = D + Temporary1;
            D = C;
            C = B;
            B = A;
            A = Temporary1 + Temporary2;
        }
        Hash[0] += A;
        Hash[1] += B;
        Hash[2] += C;
        Hash[3] += D;
        Hash[4] += E;
        Hash[5] += F;
        Hash[6] += G;
        Hash[7] += H;
    }

    for (const uint32 Word : Hash)
    {
        OutSha256 += FString::Printf(
            TEXT("%02x%02x%02x%02x"),
            static_cast<uint8>((Word >> 24u) & 0xffu),
            static_cast<uint8>((Word >> 16u) & 0xffu),
            static_cast<uint8>((Word >> 8u) & 0xffu),
            static_cast<uint8>(Word & 0xffu));
    }
    return OutSha256.Len() == 64;
}

bool RenderMasterSha256File(
    const FString& Filename,
    FString& OutSha256,
    int64* OutSizeBytes)
{
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *Filename)) return false;
    if (OutSizeBytes != nullptr) *OutSizeBytes = Bytes.Num();
    return RenderMasterSha256Bytes(Bytes, OutSha256);
}
