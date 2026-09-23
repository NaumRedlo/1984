using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace Assay;

public sealed class MapUnavailable(string message) : Exception(message);

public sealed class ChecksumMismatch(long beatmapId, string expected, string actual)
    : Exception($"beatmap {beatmapId}: osu! served {actual}, the caller expected {expected}")
{
    public string Actual { get; } = actual;
}

public interface IMapSource
{
    Task<byte[]?> Fetch(long beatmapId, CancellationToken cancellationToken);
}

public sealed class OsuWebMapSource(HttpClient client) : IMapSource
{
    public async Task<byte[]?> Fetch(long beatmapId, CancellationToken cancellationToken)
    {
        using var response = await client.GetAsync($"osu/{beatmapId}", cancellationToken);
        if (!response.IsSuccessStatusCode)
            return null;
        var body = await response.Content.ReadAsByteArrayAsync(cancellationToken);
        return body.Length < 50 ? null : body;
    }
}

public sealed partial class BeatmapStore
{
    private readonly IMapSource source;
    private readonly string root;
    private readonly TimeSpan unverifiedFor;
    private readonly ConcurrentDictionary<long, SemaphoreSlim> fetching = new();

    public BeatmapStore(IMapSource source, string root, TimeSpan unverifiedFor)
    {
        this.source = source;
        this.root = root;
        this.unverifiedFor = unverifiedFor;
        Directory.CreateDirectory(Path.Combine(root, "md5"));
        Directory.CreateDirectory(Path.Combine(root, "id"));
    }

    public async Task<(string Path, string Checksum)> Get(long beatmapId, string? checksum, CancellationToken cancellationToken)
    {
        if (beatmapId <= 0)
            throw new MapUnavailable("beatmap_id must be positive");
        var expected = Normalise(checksum);
        if (expected != null && File.Exists(ByChecksum(expected)))
            return (ByChecksum(expected), expected);

        var gate = fetching.GetOrAdd(beatmapId, _ => new SemaphoreSlim(1, 1));
        await gate.WaitAsync(cancellationToken);
        try
        {
            if (expected != null && File.Exists(ByChecksum(expected)))
                return (ByChecksum(expected), expected);

            var known = ById(beatmapId);
            if (expected == null && File.Exists(known) && DateTime.UtcNow - File.GetLastWriteTimeUtc(known) < unverifiedFor)
            {
                var kept = File.ReadAllText(known).Trim();
                if (File.Exists(ByChecksum(kept)))
                    return (ByChecksum(kept), kept);
            }

            var body = await source.Fetch(beatmapId, cancellationToken)
                       ?? throw new MapUnavailable($"osu! did not serve beatmap {beatmapId}");
            var actual = Convert.ToHexStringLower(MD5.HashData(body));
            var path = ByChecksum(actual);
            if (!File.Exists(path))
            {
                var scratch = $"{path}.{Guid.NewGuid():N}.part";
                await File.WriteAllBytesAsync(scratch, body, cancellationToken);
                File.Move(scratch, path, overwrite: true);
            }
            await File.WriteAllTextAsync(known, actual, cancellationToken);

            if (expected != null && expected != actual)
                throw new ChecksumMismatch(beatmapId, expected, actual);
            return (path, actual);
        }
        finally
        {
            gate.Release();
        }
    }

    private string ByChecksum(string checksum) => Path.Combine(root, "md5", $"{checksum}.osu");

    private string ById(long beatmapId) => Path.Combine(root, "id", $"{beatmapId}.md5");

    private static string? Normalise(string? checksum)
    {
        var value = (checksum ?? "").Trim().ToLowerInvariant();
        if (value.Length == 0)
            return null;
        if (!Md5().IsMatch(value))
            throw new MapUnavailable("checksum must be 32 hex characters");
        return value;
    }

    [GeneratedRegex("^[0-9a-f]{32}$")]
    private static partial Regex Md5();
}
