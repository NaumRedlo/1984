using System.Text.Json;
using Microsoft.Extensions.Caching.Memory;
using Xunit;
using Xunit.Abstractions;

namespace Assay.Tests;

public sealed class CorpusMaps : IMapSource
{
    public static readonly string Root = Path.Combine(AppContext.BaseDirectory, "corpus");

    public int Fetched { get; private set; }

    public Task<byte[]?> Fetch(long beatmapId, CancellationToken cancellationToken)
    {
        Fetched++;
        var path = Path.Combine(Root, "maps", $"{beatmapId}.osu");
        return Task.FromResult(File.Exists(path) ? File.ReadAllBytes(path) : null);
    }
}

public sealed class CorpusFixture : IDisposable
{
    public CorpusMaps Maps { get; } = new();
    public Calculator Calculator { get; }
    private readonly string folder = Path.Combine(Path.GetTempPath(), $"assay-tests-{Guid.NewGuid():N}");
    private readonly MemoryCache cache = new(new MemoryCacheOptions { SizeLimit = 256 });

    public CorpusFixture()
    {
        Calculator = new Calculator(new BeatmapStore(Maps, folder, TimeSpan.FromHours(1)), cache);
    }

    public static JsonElement Load(string name)
        => JsonDocument.Parse(File.ReadAllText(Path.Combine(CorpusMaps.Root, name))).RootElement;

    public void Dispose()
    {
        cache.Dispose();
        try { Directory.Delete(folder, true); } catch (IOException) { }
    }
}

public sealed class CorpusTests(CorpusFixture corpus, ITestOutputHelper output) : IClassFixture<CorpusFixture>
{
    private const double PpTolerance = 0.001;
    private const double StarTolerance = 0.001;

    private const double HiddenFlashlightTolerance = 0.015;

    private static ScoreRequest RequestFor(JsonElement entry, bool withAccuracy)
    {
        var statistics = entry.GetProperty("statistics").EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetInt32());
        long legacyTotal = entry.GetProperty("legacy_total_score").GetInt64();
        return new ScoreRequest
        {
            BeatmapId = entry.GetProperty("beatmap_id").GetInt64(),
            Mods = ModListConverter.Acronyms(entry.GetProperty("mods").GetString()).ToList(),
            Statistics = statistics,
            MaxCombo = entry.GetProperty("combo").GetInt32(),
            Accuracy = withAccuracy ? entry.GetProperty("accuracy").GetDouble() / 100 : null,
            LegacyTotalScore = legacyTotal > 0 ? legacyTotal : null,
        };
    }

    [Fact]
    public async Task Every_score_in_the_corpus_is_worth_what_osu_tools_said()
    {
        var scores = CorpusFixture.Load("scores.json").GetProperty("scores").EnumerateArray().ToList();
        Assert.Equal(400, scores.Count);
        var off = new List<string>();
        double worst = 0;
        foreach (var entry in scores)
        {
            var result = await corpus.Calculator.Score(RequestFor(entry, withAccuracy: false), CancellationToken.None);
            double expected = entry.GetProperty("pp").GetDouble();
            double relative = Math.Abs(result.Pp - expected) / Math.Max(expected, 1);
            worst = Math.Max(worst, relative);
            if (relative > PpTolerance)
                off.Add($"{entry.GetProperty("beatmap_id")} {entry.GetProperty("mods")} {entry.GetProperty("play")}: {result.Pp:F3} vs {expected:F3}");
        }
        output.WriteLine($"worst relative difference: {worst:P4}");
        Assert.True(off.Count == 0, $"{off.Count} of {scores.Count} scores differ:\n" + string.Join("\n", off.Take(30)));
    }

    [Fact]
    public async Task The_accuracy_worked_out_from_the_hits_is_the_one_osu_tools_used()
    {
        foreach (var entry in CorpusFixture.Load("scores.json").GetProperty("scores").EnumerateArray())
        {
            var result = await corpus.Calculator.Score(RequestFor(entry, withAccuracy: false), CancellationToken.None);
            Assert.Equal(entry.GetProperty("accuracy").GetDouble() / 100, result.Accuracy, 6);
        }
    }

    [Fact]
    public async Task A_given_accuracy_is_taken_as_it_is()
    {
        var entry = CorpusFixture.Load("scores.json").GetProperty("scores").EnumerateArray().First(e => e.GetProperty("play").GetString() == "good");
        var result = await corpus.Calculator.Score(RequestFor(entry, withAccuracy: true), CancellationToken.None);
        Assert.Equal(entry.GetProperty("pp").GetDouble(), result.Pp, 3);
    }

    [Fact]
    public async Task Every_star_rating_is_the_one_osu_reports()
    {
        var off = new List<string>();
        foreach (var map in CorpusFixture.Load("expected.json").GetProperty("maps").EnumerateArray())
        {
            foreach (var attributes in map.GetProperty("attributes").EnumerateObject())
            {
                var result = await corpus.Calculator.Map(new MapRequest
                {
                    BeatmapId = map.GetProperty("beatmap_id").GetInt64(),
                    Mods = ModListConverter.Acronyms(attributes.Name).ToList(),
                }, CancellationToken.None);
                double expected = attributes.Value.GetProperty("star_rating").GetDouble();
                double allowed = attributes.Name == "HDFL" ? HiddenFlashlightTolerance * expected : StarTolerance;
                if (Math.Abs(result.StarRating - expected) > allowed)
                    off.Add($"{map.GetProperty("beatmap_id")} {attributes.Name}: {result.StarRating:F4} vs {expected:F4}");
                Assert.Equal(attributes.Value.GetProperty("max_combo").GetInt32(), result.MaxCombo);
            }
        }
        Assert.True(off.Count == 0, string.Join("\n", off));
    }

    [Fact]
    public async Task A_perfect_play_is_what_if_ss_says_for_its_imperfect_neighbours()
    {
        var scores = CorpusFixture.Load("scores.json").GetProperty("scores").EnumerateArray().ToList();
        foreach (var perfect in scores.Where(e => e.GetProperty("play").GetString() == "perfect"))
        {
            var sibling = scores.First(e =>
                e.GetProperty("beatmap_id").GetInt64() == perfect.GetProperty("beatmap_id").GetInt64()
                && e.GetProperty("mods").GetString() == perfect.GetProperty("mods").GetString()
                && e.GetProperty("play").GetString() == "a few misses");
            var result = await corpus.Calculator.Score(RequestFor(sibling, withAccuracy: false), CancellationToken.None);
            double expected = perfect.GetProperty("pp").GetDouble();
            Assert.True(Math.Abs(result.PpIfSs!.Value - expected) / expected < 0.005,
                $"{perfect.GetProperty("beatmap_id")} {perfect.GetProperty("mods")}: SS {result.PpIfSs:F2} vs perfect {expected:F2}");
            Assert.True(result.PpIfFc >= result.Pp);
        }
    }
}
