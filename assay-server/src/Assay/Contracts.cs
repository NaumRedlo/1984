using System.Text.Json;
using System.Text.Json.Serialization;

namespace Assay;

public sealed record ModInput(string Acronym, Dictionary<string, JsonElement>? Settings = null);

public sealed record MapRequest
{
    public long BeatmapId { get; init; }
    public string? Checksum { get; init; }
    public int Ruleset { get; init; }

    [JsonConverter(typeof(ModListConverter))]
    public List<ModInput> Mods { get; init; } = [];
}

public sealed record ScoreRequest
{
    public long BeatmapId { get; init; }
    public string? Checksum { get; init; }
    public int Ruleset { get; init; }

    [JsonConverter(typeof(ModListConverter))]
    public List<ModInput> Mods { get; init; } = [];

    public Dictionary<string, int> Statistics { get; init; } = [];
    public double? Accuracy { get; init; }
    public int? MaxCombo { get; init; }
    public long? LegacyTotalScore { get; init; }
    public bool? IsLegacy { get; init; }
}

public sealed record WhatIfRequest
{
    public long BeatmapId { get; init; }
    public string? Checksum { get; init; }
    public int Ruleset { get; init; }

    [JsonConverter(typeof(ModListConverter))]
    public List<ModInput> Mods { get; init; } = [];

    public List<double> Accuracies { get; init; } = [];
    public int Misses { get; init; }
}

public sealed record MapResult(
    long BeatmapId,
    string Checksum,
    int Ruleset,
    double StarRating,
    int MaxCombo,
    double ClockRate,
    Dictionary<string, double> Attributes);

public sealed record ScoreResult(
    double Pp,
    double? PpIfFc,
    double? PpIfSs,
    double Accuracy,
    int MaxCombo,
    double StarRating,
    Dictionary<string, double?> Performance,
    MapResult Map);

public sealed record WhatIfPoint(double Accuracy, double Pp, Dictionary<string, int> Statistics);

public sealed record WhatIfResult(List<WhatIfPoint> Points, MapResult Map);

public sealed record Health(string Status, string OsuVersion, string[] Rulesets);

public sealed record Problem(string Error, string? Detail = null);

public sealed class ModListConverter : JsonConverter<List<ModInput>>
{
    public override List<ModInput> Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        var mods = new List<ModInput>();
        if (reader.TokenType == JsonTokenType.Null)
            return mods;
        if (reader.TokenType == JsonTokenType.String)
        {
            mods.AddRange(Acronyms(reader.GetString()));
            return mods;
        }
        if (reader.TokenType != JsonTokenType.StartArray)
            throw new JsonException("mods must be a list or a string");

        while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
        {
            if (reader.TokenType == JsonTokenType.String)
            {
                mods.AddRange(Acronyms(reader.GetString()));
                continue;
            }
            using var element = JsonDocument.ParseValue(ref reader);
            var root = element.RootElement;
            if (!root.TryGetProperty("acronym", out var acronym) || acronym.ValueKind != JsonValueKind.String)
                throw new JsonException("a mod needs an acronym");
            Dictionary<string, JsonElement>? settings = null;
            if (root.TryGetProperty("settings", out var given) && given.ValueKind == JsonValueKind.Object)
                settings = given.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.Clone());
            mods.Add(new ModInput(acronym.GetString()!.ToUpperInvariant(), settings));
        }
        return mods;
    }

    public override void Write(Utf8JsonWriter writer, List<ModInput> value, JsonSerializerOptions options)
        => JsonSerializer.Serialize(writer, value.ToArray(), options);

    internal static IEnumerable<ModInput> Acronyms(string? text)
    {
        var joined = (text ?? "").ToUpperInvariant();
        foreach (var part in joined.Split([',', ' ', '+'], StringSplitOptions.RemoveEmptyEntries))
        {
            if (part.Length % 2 == 1 && part.Length > 3)
                throw new JsonException($"cannot read mods from '{part}'");
            if (part.Length <= 3)
            {
                if (part != "NM")
                    yield return new ModInput(part);
                continue;
            }
            for (int i = 0; i < part.Length; i += 2)
            {
                var acronym = part.Substring(i, 2);
                if (acronym != "NM")
                    yield return new ModInput(acronym);
            }
        }
    }
}
