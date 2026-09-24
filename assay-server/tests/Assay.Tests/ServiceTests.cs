using System.Text.Json;
using Microsoft.Extensions.Caching.Memory;
using osu.Game.Rulesets.Scoring;
using Xunit;

namespace Assay.Tests;

public sealed class ServiceTests(CorpusFixture corpus) : IClassFixture<CorpusFixture>
{
    private const long Map = 1494828;

    [Fact]
    public async Task A_rate_setting_is_honoured()
    {
        var plain = await corpus.Calculator.Map(new MapRequest { BeatmapId = Map, Mods = [new ModInput("DT")] }, CancellationToken.None);
        var slower = await corpus.Calculator.Map(new MapRequest
        {
            BeatmapId = Map,
            Mods = [new ModInput("DT", new() { ["speed_change"] = JsonDocument.Parse("1.2").RootElement })],
        }, CancellationToken.None);
        Assert.Equal(1.5, plain.ClockRate, 6);
        Assert.Equal(1.2, slower.ClockRate, 6);
        Assert.True(slower.StarRating < plain.StarRating);
    }

    [Fact]
    public async Task Mods_are_read_from_a_string_a_list_or_objects()
    {
        var options = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        foreach (var body in new[]
                 {
                     """{"beatmap_id":1494828,"mods":"HDDT"}""",
                     """{"beatmap_id":1494828,"mods":["HD","DT"]}""",
                     """{"beatmap_id":1494828,"mods":[{"acronym":"HD"},{"acronym":"dt","settings":{}}]}""",
                 })
        {
            var request = JsonSerializer.Deserialize<MapRequest>(body, options)!;
            Assert.Equal(["HD", "DT"], request.Mods.Select(m => m.Acronym));
        }
    }

    [Fact]
    public async Task Unknown_or_clashing_mods_are_refused()
    {
        await Assert.ThrowsAsync<BadRequest>(() => corpus.Calculator.Map(new MapRequest { BeatmapId = Map, Mods = [new ModInput("ZZ")] }, CancellationToken.None));
        await Assert.ThrowsAsync<BadRequest>(() => corpus.Calculator.Map(new MapRequest { BeatmapId = Map, Mods = [new ModInput("HR"), new ModInput("EZ")] }, CancellationToken.None));
        await Assert.ThrowsAsync<BadRequest>(() => corpus.Calculator.Map(new MapRequest { BeatmapId = Map, Ruleset = 9 }, CancellationToken.None));
    }

    [Fact]
    public async Task A_map_that_changed_is_noticed_through_its_checksum()
    {
        var folder = Path.Combine(Path.GetTempPath(), $"assay-checksum-{Guid.NewGuid():N}");
        try
        {
            var maps = new CorpusMaps();
            var store = new BeatmapStore(maps, folder, TimeSpan.FromHours(1));
            var (_, actual) = await store.Get(Map, null, CancellationToken.None);
            await store.Get(Map, actual, CancellationToken.None);
            await store.Get(Map, actual.ToUpperInvariant(), CancellationToken.None);
            Assert.Equal(1, maps.Fetched);

            var mismatch = await Assert.ThrowsAsync<ChecksumMismatch>(() => store.Get(Map, new string('0', 32), CancellationToken.None));
            Assert.Equal(actual, mismatch.Actual);
            await Assert.ThrowsAsync<MapUnavailable>(() => store.Get(Map, "../etc/passwd", CancellationToken.None));
            await Assert.ThrowsAsync<MapUnavailable>(() => store.Get(999, null, CancellationToken.None));
        }
        finally
        {
            Directory.Delete(folder, true);
        }
    }

    [Fact]
    public async Task Strains_follow_the_map_and_its_speed()
    {
        var plain = await corpus.Calculator.Strains(new StrainsRequest { BeatmapId = Map }, CancellationToken.None);
        var faster = await corpus.Calculator.Strains(new StrainsRequest { BeatmapId = Map, Mods = [new ModInput("DT")], Points = 500 }, CancellationToken.None);

        Assert.Equal(64, plain.Strains.Count);
        Assert.All(plain.Strains, v => Assert.InRange(v, 0, 1));
        Assert.True(plain.Strains.Max() > 0.5, "averaging keeps the hardest stretch near the top");
        Assert.True(plain.Strains.Distinct().Count() > 10, "a real map is not flat");
        // 400 ms sections of the played time: DT plays the same map in two thirds of it
        Assert.InRange(faster.Sections, plain.Sections * 2 / 3 - 2, plain.Sections * 2 / 3 + 2);
        Assert.Equal(Math.Min(500, faster.Sections), faster.Strains.Count);
        Assert.Equal(1.5, faster.Map.ClockRate, 6);
        await Assert.ThrowsAsync<BadRequest>(() => corpus.Calculator.Strains(new StrainsRequest { BeatmapId = Map, Points = 0 }, CancellationToken.None));
    }

    [Fact]
    public void Strains_are_scaled_and_averaged_down()
    {
        Assert.Equal([0.5, 1.0], Strains.Shape([1, 2], 64));
        Assert.Equal([0.125, 0.75], Strains.Shape([0, 1, 2, 4], 2));
        Assert.Empty(Strains.Shape([], 64));
    }

    [Fact]
    public async Task What_if_climbs_with_accuracy_and_lands_where_asked()
    {
        var result = await corpus.Calculator.WhatIf(new WhatIfRequest { BeatmapId = Map, Accuracies = [0.95, 98, 0.99, 1.0] }, CancellationToken.None);
        Assert.Equal(4, result.Points.Count);
        for (int i = 1; i < result.Points.Count; i++)
            Assert.True(result.Points[i].Pp > result.Points[i - 1].Pp);
        Assert.Equal(0.95, result.Points[0].Accuracy, 2);
        Assert.Equal(0.98, result.Points[1].Accuracy, 2);
        Assert.Equal(1.0, result.Points[3].Accuracy, 6);
        var perfect = await corpus.Calculator.Score(new ScoreRequest { BeatmapId = Map, Statistics = new() { ["great"] = 1704 } }, CancellationToken.None);
        Assert.Equal(perfect.Pp, result.Points[3].Pp, 6);
    }

    [Fact]
    public async Task A_failed_play_is_finished_with_greats_for_if_fc()
    {
        var failed = await corpus.Calculator.Score(new ScoreRequest
        {
            BeatmapId = Map,
            Statistics = new() { ["great"] = 131, ["ok"] = 41, ["meh"] = 4, ["miss"] = 2 },
            MaxCombo = 125,
        }, CancellationToken.None);
        var finished = await corpus.Calculator.Score(new ScoreRequest
        {
            BeatmapId = Map,
            Statistics = new() { ["great"] = 1704 - 45, ["ok"] = 41, ["meh"] = 4 },
        }, CancellationToken.None);
        Assert.Equal(finished.Pp, failed.PpIfFc!.Value, 6);
        Assert.True(failed.PpIfFc > failed.Pp * 3);
    }

    [Fact]
    public void Simulated_hits_always_add_up()
    {
        var maximum = new Dictionary<HitResult, int> { [HitResult.Great] = 1000, [HitResult.SliderTailHit] = 300 };
        foreach (var accuracy in new[] { 0.0, 0.1, 0.2, 0.5, 0.9, 0.97, 1.0 })
        foreach (var misses in new[] { 0, 3, 999, 5000 })
        {
            var hits = Calculator.Simulate(maximum, accuracy, misses);
            Assert.Equal(1000, hits[HitResult.Great] + hits[HitResult.Ok] + hits[HitResult.Meh] + hits[HitResult.Miss]);
            Assert.All(hits.Values, v => Assert.True(v >= 0));
        }
    }

    [Fact]
    public void Statistics_are_read_by_their_api_and_their_old_names()
    {
        var read = Calculator.ReadStatistics(new() { ["great"] = 1, ["count_100"] = 2, ["large_tick_miss"] = 3, ["slider_tail_hit"] = 4, ["legacy_combo_increase"] = 5 });
        Assert.Equal(1, read[HitResult.Great]);
        Assert.Equal(2, read[HitResult.Ok]);
        Assert.Equal(3, read[HitResult.LargeTickMiss]);
        Assert.Equal(4, read[HitResult.SliderTailHit]);
        Assert.Equal(5, read[HitResult.LegacyComboIncrease]);
        Assert.Throws<BadRequest>(() => Calculator.ReadStatistics(new() { ["nonsense"] = 1 }));
        Assert.Throws<BadRequest>(() => Calculator.ReadStatistics(new() { ["great"] = -1 }));
    }
}
