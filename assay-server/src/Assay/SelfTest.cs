using System.Text;
using Microsoft.Extensions.Caching.Memory;

namespace Assay;

internal static class SelfTest
{
    private sealed class Fixed(byte[] body) : IMapSource
    {
        public Task<byte[]?> Fetch(long beatmapId, CancellationToken cancellationToken) => Task.FromResult<byte[]?>(body);
    }

    public static async Task<int> Run()
    {
        var folder = Path.Combine(Path.GetTempPath(), $"assay-self-test-{Environment.ProcessId}");
        try
        {
            var store = new BeatmapStore(new Fixed(Encoding.UTF8.GetBytes(Map())), folder, TimeSpan.FromHours(1));
            using var cache = new MemoryCache(new MemoryCacheOptions { SizeLimit = 8 });
            var calculator = new Calculator(store, cache);
            var score = await calculator.Score(new ScoreRequest
            {
                BeatmapId = 1,
                Mods = [new ModInput("HD")],
                Statistics = new() { ["great"] = 60, ["ok"] = 4 },
            }, CancellationToken.None);
            bool sane = score.Map.StarRating > 0 && score.Pp > 0 && score.PpIfSs >= score.Pp;
            Console.WriteLine($"assay self-test: osu! {Calculator.OsuVersion}, {score.Map.StarRating:F2}*, {score.Pp:F2}pp (FC {score.PpIfFc:F2}, SS {score.PpIfSs:F2}, acc {score.Accuracy:F4}) — {(sane ? "ok" : "WRONG")}");
            return sane ? 0 : 1;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine($"assay self-test failed: {exception}");
            return 1;
        }
        finally
        {
            try { Directory.Delete(folder, true); } catch (IOException) { }
        }
    }

    private static string Map()
    {
        var map = new StringBuilder("""
            osu file format v14

            [General]
            Mode: 0

            [Difficulty]
            HPDrainRate:5
            CircleSize:4
            OverallDifficulty:8
            ApproachRate:9
            SliderMultiplier:1.4
            SliderTickRate:1

            [TimingPoints]
            0,300,4,2,0,60,1,0

            [HitObjects]

            """);
        for (int i = 0; i < 64; i++)
        {
            int x = 64 + (i * 97) % 384, y = 48 + (i * 61) % 288;
            map.Append($"{x},{y},{1000 + i * 150},1,0\n");
        }
        return map.ToString();
    }
}
