using System.Reflection;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Caching.Memory;
using osu.Game.Beatmaps;
using osu.Game.Database;
using osu.Game.Online.API;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Catch;
using osu.Game.Rulesets.Difficulty;
using osu.Game.Rulesets.Mania;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Scoring;
using osu.Game.Rulesets.Taiko;
using osu.Game.Rulesets.UI;
using osu.Game.Scoring;
using osu.Game.Rulesets.Objects;
using osu.Game.Utils;

namespace Assay;

public sealed class BadRequest(string message) : Exception(message);

public sealed class Calculator(BeatmapStore store, IMemoryCache cache)
{
    public static readonly string OsuVersion =
        typeof(OsuRuleset).Assembly.GetName().Version?.ToString(3) ?? "unknown";

    private static readonly Ruleset[] rulesets =
    [
        new OsuRuleset(),
        new TaikoRuleset(),
        new CatchRuleset(),
        new ManiaRuleset(),
    ];

    public static string[] RulesetNames => rulesets.Select(r => r.ShortName).ToArray();

    private sealed record Prepared(
        long BeatmapId,
        string Checksum,
        Ruleset Ruleset,
        Mod[] Mods,
        WorkingBeatmap Working,
        DifficultyAttributes Difficulty,
        Dictionary<HitResult, int> Maximum);

    public async Task<MapResult> Map(MapRequest request, CancellationToken cancellationToken)
    {
        var prepared = await Prepare(request.BeatmapId, request.Checksum, request.Ruleset, request.Mods, cancellationToken);
        return Describe(prepared);
    }

    public async Task<ScoreResult> Score(ScoreRequest request, CancellationToken cancellationToken)
    {
        var prepared = await Prepare(request.BeatmapId, request.Checksum, request.Ruleset, request.Mods, cancellationToken);
        bool legacy = request.IsLegacy ?? request.LegacyTotalScore != null;
        var statistics = ReadStatistics(request.Statistics);
        if (statistics.Count == 0)
            throw new BadRequest("statistics are empty");
        if (!legacy)
            FillUnreported(statistics, prepared.Maximum);

        var played = Build(prepared, statistics, request.Accuracy, request.MaxCombo, request.LegacyTotalScore, legacy);
        var performance = Perform(prepared, played);

        double? ifFc = null, ifSs = null;
        if (prepared.Ruleset is OsuRuleset)
        {
            ifFc = Perform(prepared, Build(prepared, FullCombo(statistics, prepared.Maximum, legacy), null, null, null, legacy)).Total;
            ifSs = Perform(prepared, Build(prepared, Perfect(prepared.Maximum, legacy), null, null, null, legacy)).Total;
        }

        return new ScoreResult(
            performance.Total,
            ifFc,
            ifSs,
            played.Accuracy,
            played.MaxCombo,
            prepared.Difficulty.StarRating,
            Numbers(performance),
            Describe(prepared));
    }

    public async Task<WhatIfResult> WhatIf(WhatIfRequest request, CancellationToken cancellationToken)
    {
        var prepared = await Prepare(request.BeatmapId, request.Checksum, request.Ruleset, request.Mods, cancellationToken);
        if (prepared.Ruleset is not OsuRuleset)
            throw new BadRequest("what-if is only simulated for osu!standard");
        if (request.Accuracies.Count is 0 or > 20)
            throw new BadRequest("give between 1 and 20 accuracies");

        var points = new List<WhatIfPoint>();
        foreach (var wanted in request.Accuracies)
        {
            var accuracy = wanted > 1 ? wanted / 100 : wanted;
            if (accuracy is < 0 or > 1 || double.IsNaN(accuracy))
                throw new BadRequest($"accuracy {wanted} is out of range");
            var statistics = Aim(prepared, accuracy, Math.Max(0, request.Misses));
            var combo = statistics.GetValueOrDefault(HitResult.Miss) > 0 ? (int?)null : prepared.Difficulty.MaxCombo;
            var score = Build(prepared, statistics, null, combo, null, false);
            points.Add(new WhatIfPoint(score.Accuracy, Perform(prepared, score).Total, WriteStatistics(statistics)));
        }
        return new WhatIfResult(points, Describe(prepared));
    }

    public async Task<StrainsResult> Strains(StrainsRequest request, CancellationToken cancellationToken)
    {
        if (request.Points is < 1 or > 1000)
            throw new BadRequest("points must be 1..1000");
        var prepared = await Prepare(request.BeatmapId, request.Checksum, request.Ruleset, request.Mods, cancellationToken);
        var key = $"strains|{prepared.Checksum}|{request.Ruleset}|{ModKey(prepared.Mods)}";
        var sections = await cache.GetOrCreateAsync(key, entry =>
        {
            entry.Size = 1;
            entry.SlidingExpiration = TimeSpan.FromHours(6);
            return Task.Run(() => Assay.Strains.Sections(prepared.Ruleset, prepared.Working, prepared.Mods), cancellationToken);
        });
        return new StrainsResult(Assay.Strains.Shape(sections!, request.Points), sections!.Count, Describe(prepared));
    }

    private async Task<Prepared> Prepare(long beatmapId, string? checksum, int rulesetId, List<ModInput> modInputs, CancellationToken cancellationToken)
    {
        if (rulesetId < 0 || rulesetId >= rulesets.Length)
            throw new BadRequest($"ruleset must be 0..{rulesets.Length - 1}");
        var ruleset = rulesets[rulesetId];
        var mods = ParseMods(ruleset, modInputs);
        var (path, md5) = await store.Get(beatmapId, checksum, cancellationToken);
        var key = $"{md5}|{rulesetId}|{ModKey(mods)}";

        var prepared = await cache.GetOrCreateAsync(key, entry =>
        {
            entry.Size = 1;
            entry.SlidingExpiration = TimeSpan.FromHours(6);
            return Task.Run(() => PrepareNow(beatmapId, md5, path, ruleset, mods), cancellationToken);
        });
        return prepared!;
    }

    private static Prepared PrepareNow(long beatmapId, string md5, string path, Ruleset ruleset, Mod[] mods)
    {
        var working = new FlatWorkingBeatmap(path, (int)beatmapId);
        IBeatmap playable;
        try
        {
            playable = working.GetPlayableBeatmap(ruleset.RulesetInfo, mods);
        }
        catch (Exception exception) when (exception is BeatmapInvalidForRulesetException or InvalidOperationException)
        {
            throw new BadRequest($"beatmap {beatmapId} cannot be played as {ruleset.ShortName}: {exception.Message}");
        }

        var difficulty = ruleset.CreateDifficultyCalculator(working).Calculate(mods);
        return new Prepared(beatmapId, md5, ruleset, mods, working, difficulty, MaximumOf(playable));
    }

    internal static Dictionary<HitResult, int> MaximumOf(IBeatmap playable)
    {
        var maximum = new Dictionary<HitResult, int>();
        foreach (var hitObject in playable.HitObjects)
            Visit(hitObject);
        return maximum;

        void Visit(HitObject hitObject)
        {
            foreach (var nested in hitObject.NestedHitObjects)
                Visit(nested);
            var result = hitObject.Judgement.MaxResult;
            if (result.IsScorable() && !result.IsBonus())
                maximum[result] = maximum.GetValueOrDefault(result) + 1;
        }
    }

    private static ScoreInfo Build(Prepared prepared, Dictionary<HitResult, int> statistics, double? accuracy, int? combo, long? legacyTotal, bool legacy)
    {
        var score = new ScoreInfo(prepared.Working.BeatmapInfo, prepared.Ruleset.RulesetInfo)
        {
            Mods = prepared.Mods,
            Statistics = statistics,
            MaximumStatistics = new Dictionary<HitResult, int>(prepared.Maximum),
            MaxCombo = Math.Clamp(combo ?? prepared.Difficulty.MaxCombo, 0, Math.Max(prepared.Difficulty.MaxCombo, 0)),
            LegacyTotalScore = legacyTotal,
            IsLegacyScore = legacy,
        };
        score.Accuracy = accuracy is { } given
            ? (given > 1 ? given / 100 : given)
            : ComputeAccuracy(prepared, statistics, legacy);
        return score;
    }

    private static double ComputeAccuracy(Prepared prepared, Dictionary<HitResult, int> statistics, bool legacy)
    {
        if (legacy && prepared.Ruleset is OsuRuleset)
        {
            int great = statistics.GetValueOrDefault(HitResult.Great);
            int ok = statistics.GetValueOrDefault(HitResult.Ok);
            int meh = statistics.GetValueOrDefault(HitResult.Meh);
            int miss = statistics.GetValueOrDefault(HitResult.Miss);
            int total = great + ok + meh + miss;
            return total == 0 ? 1 : (300.0 * great + 100.0 * ok + 50.0 * meh) / (300.0 * total);
        }
        return StandardisedScoreMigrationTools.ComputeAccuracy(statistics, prepared.Maximum, prepared.Ruleset.CreateScoreProcessor());
    }

    private static PerformanceAttributes Perform(Prepared prepared, ScoreInfo score)
        => prepared.Ruleset.CreatePerformanceCalculator()!.Calculate(score, prepared.Difficulty);

    private static void FillUnreported(Dictionary<HitResult, int> statistics, Dictionary<HitResult, int> maximum)
    {
        Fill(HitResult.LargeTickHit, HitResult.LargeTickMiss);
        Fill(HitResult.SmallTickHit, HitResult.SmallTickMiss);
        Fill(HitResult.SliderTailHit, null);

        void Fill(HitResult hit, HitResult? miss)
        {
            if (statistics.ContainsKey(hit) || !maximum.TryGetValue(hit, out int most))
                return;
            int missed = miss is { } m ? statistics.GetValueOrDefault(m) : 0;
            statistics[hit] = Math.Max(0, most - missed);
        }
    }

    private static Dictionary<HitResult, int> FullCombo(Dictionary<HitResult, int> played, Dictionary<HitResult, int> maximum, bool legacy)
    {
        var statistics = new Dictionary<HitResult, int>(played);
        int objects = maximum.Where(pair => pair.Key.IsBasic()).Sum(pair => pair.Value);
        int ok = statistics.GetValueOrDefault(HitResult.Ok);
        int meh = statistics.GetValueOrDefault(HitResult.Meh);
        statistics[HitResult.Great] = Math.Max(0, objects - ok - meh);
        statistics[HitResult.Miss] = 0;
        if (!legacy)
        {
            foreach (var (hit, miss) in new[] { (HitResult.LargeTickHit, HitResult.LargeTickMiss), (HitResult.SmallTickHit, HitResult.SmallTickMiss) })
            {
                if (maximum.TryGetValue(hit, out int most))
                    statistics[hit] = most;
                statistics.Remove(miss);
            }
            if (maximum.TryGetValue(HitResult.SliderTailHit, out int tails))
                statistics[HitResult.SliderTailHit] = tails;
        }
        return statistics;
    }

    private static Dictionary<HitResult, int> Perfect(Dictionary<HitResult, int> maximum, bool legacy)
    {
        if (!legacy)
            return new Dictionary<HitResult, int>(maximum);
        int objects = maximum.Where(pair => pair.Key.IsBasic()).Sum(pair => pair.Value);
        return new Dictionary<HitResult, int> { [HitResult.Great] = objects };
    }

    private static Dictionary<HitResult, int> Aim(Prepared prepared, double accuracy, int misses)
    {
        double low = 0, high = 1;
        var best = Simulate(prepared.Maximum, accuracy, misses);
        double bestOff = Math.Abs(ComputeAccuracy(prepared, best, false) - accuracy);
        for (int step = 0; step < 40 && bestOff > 1e-6; step++)
        {
            double middle = (low + high) / 2;
            var statistics = Simulate(prepared.Maximum, middle, misses);
            double reached = ComputeAccuracy(prepared, statistics, false);
            double off = Math.Abs(reached - accuracy);
            if (off < bestOff)
                (best, bestOff) = (statistics, off);
            if (reached < accuracy)
                low = middle;
            else
                high = middle;
        }
        return best;
    }

    internal static Dictionary<HitResult, int> Simulate(Dictionary<HitResult, int> maximum, double accuracy, int misses)
    {
        int total = maximum.Where(pair => pair.Key.IsBasic()).Sum(pair => pair.Value);
        misses = Math.Min(misses, total);
        int relevant = total - misses;
        int ok = 0, meh = 0;
        if (relevant > 0)
        {
            double wanted = Math.Clamp(accuracy * total / relevant, 0, 1);
            if (wanted >= 0.25)
            {
                double ratio = Math.Pow(1 - (wanted - 0.25) / 0.75, 2);
                double oks = 6 * relevant * (1 - wanted) / (5 * ratio + 4);
                ok = (int)Math.Round(oks);
                meh = (int)Math.Round(oks + oks * ratio) - ok;
            }
            else if (wanted >= 1.0 / 6)
            {
                ok = (int)Math.Round(6 * relevant * wanted - relevant);
                meh = relevant - ok;
            }
            else
            {
                meh = relevant;
            }
        }
        ok = Math.Clamp(ok, 0, relevant);
        meh = Math.Clamp(meh, 0, relevant - ok);

        var statistics = new Dictionary<HitResult, int>(maximum)
        {
            [HitResult.Great] = relevant - ok - meh,
            [HitResult.Ok] = ok,
            [HitResult.Meh] = meh,
            [HitResult.Miss] = misses,
        };
        return statistics;
    }

    private static Mod[] ParseMods(Ruleset ruleset, List<ModInput> inputs)
    {
        var mods = new List<Mod>();
        foreach (var input in inputs)
        {
            var api = new APIMod
            {
                Acronym = input.Acronym,
                Settings = (input.Settings ?? []).ToDictionary(pair => pair.Key, pair => Plain(pair.Value)),
            };
            Mod mod;
            try
            {
                mod = api.ToMod(ruleset);
            }
            catch (Exception exception)
            {
                throw new BadRequest($"mod {input.Acronym}: {exception.Message}");
            }
            if (mod is UnknownMod)
                throw new BadRequest($"{ruleset.ShortName} has no mod {input.Acronym}");
            mods.Add(mod);
        }
        if (!ModUtils.CheckCompatibleSet(mods, out var invalid))
            throw new BadRequest($"mods do not go together: {string.Join(", ", invalid!.Select(m => m.Acronym))}");
        return mods.ToArray();
    }

    private static object Plain(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Number => value.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.String => value.GetString()!,
        _ => throw new BadRequest("mod settings are numbers, booleans or strings"),
    };

    private static string ModKey(Mod[] mods)
    {
        var key = new StringBuilder();
        foreach (var api in mods.Select(m => new APIMod(m)).OrderBy(m => m.Acronym, StringComparer.Ordinal))
        {
            key.Append(api.Acronym);
            foreach (var (name, value) in api.Settings.OrderBy(p => p.Key, StringComparer.Ordinal))
                key.Append(';').Append(name).Append('=').Append(Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture));
            key.Append('|');
        }
        return key.ToString();
    }

    private static MapResult Describe(Prepared prepared)
        => new(
            prepared.BeatmapId,
            prepared.Checksum,
            prepared.Ruleset.RulesetInfo.OnlineID,
            prepared.Difficulty.StarRating,
            prepared.Difficulty.MaxCombo,
            ModUtils.CalculateRateWithMods(prepared.Mods),
            Numbers(prepared.Difficulty).Where(p => p.Value.HasValue).ToDictionary(p => p.Key, p => p.Value!.Value));

    private static Dictionary<string, double?> Numbers(object attributes)
    {
        var numbers = new Dictionary<string, double?>();
        foreach (var property in attributes.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            var type = Nullable.GetUnderlyingType(property.PropertyType) ?? property.PropertyType;
            if (type != typeof(double) && type != typeof(int) && type != typeof(float))
                continue;
            var value = property.GetValue(attributes);
            numbers[Snake(property.Name)] = value == null ? null : Convert.ToDouble(value);
        }
        return numbers;
    }

    internal static Dictionary<HitResult, int> ReadStatistics(Dictionary<string, int> given)
    {
        var statistics = new Dictionary<HitResult, int>();
        foreach (var (name, count) in given)
        {
            if (count < 0)
                throw new BadRequest($"statistic {name} is negative");
            var result = Enum.GetValues<HitResult>().FirstOrDefault(r => Snake(r.ToString()) == name.ToLowerInvariant(), HitResult.None);
            var legacyName = name.ToLowerInvariant() switch
            {
                "count_300" => HitResult.Great,
                "count_100" => HitResult.Ok,
                "count_50" => HitResult.Meh,
                "count_miss" => HitResult.Miss,
                _ => HitResult.None,
            };
            result = result == HitResult.None ? legacyName : result;
            if (result == HitResult.None)
                throw new BadRequest($"unknown statistic {name}");
            statistics[result] = statistics.GetValueOrDefault(result) + count;
        }
        return statistics;
    }

    internal static Dictionary<string, int> WriteStatistics(Dictionary<HitResult, int> statistics)
        => statistics.Where(p => p.Value != 0).ToDictionary(p => Snake(p.Key.ToString()), p => p.Value);

    internal static string Snake(string name)
    {
        var text = new StringBuilder();
        for (int i = 0; i < name.Length; i++)
        {
            if (char.IsUpper(name[i]) && i > 0)
                text.Append('_');
            text.Append(char.ToLowerInvariant(name[i]));
        }
        return text.ToString();
    }
}
