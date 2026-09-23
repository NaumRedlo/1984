using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Assay;
using Microsoft.Extensions.Caching.Memory;

if (args.Contains("--self-test"))
    return await SelfTest.Run();

var builder = WebApplication.CreateSlimBuilder(args);
builder.WebHost.UseUrls(Environment.GetEnvironmentVariable("ASSAY_URLS") ?? "http://127.0.0.1:5077");
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    options.SerializerOptions.DictionaryKeyPolicy = null;
    options.SerializerOptions.NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowNamedFloatingPointLiterals;
});
builder.Services.AddMemoryCache(options => options.SizeLimit = Settings.Int("ASSAY_CACHED_MAPS", 512));
builder.Services.AddHttpClient<IMapSource, OsuWebMapSource>(client =>
{
    client.BaseAddress = new Uri(Environment.GetEnvironmentVariable("ASSAY_OSU_URL") ?? "https://osu.ppy.sh/");
    client.Timeout = TimeSpan.FromSeconds(20);
    client.DefaultRequestHeaders.UserAgent.ParseAdd("1984-assay");
});
builder.Services.AddSingleton(services => new BeatmapStore(
    services.GetRequiredService<IMapSource>(),
    Environment.GetEnvironmentVariable("ASSAY_MAPS") ?? Path.Combine(AppContext.BaseDirectory, "maps"),
    TimeSpan.FromHours(Settings.Int("ASSAY_UNVERIFIED_HOURS", 24))));
builder.Services.AddSingleton(services => new Calculator(
    services.GetRequiredService<BeatmapStore>(),
    services.GetRequiredService<IMemoryCache>()));

var app = builder.Build();
var token = Environment.GetEnvironmentVariable("ASSAY_TOKEN") ?? "";
var working = new SemaphoreSlim(Settings.Int("ASSAY_WORKERS", Environment.ProcessorCount));

app.Use(async (context, next) =>
{
    if (token.Length > 0 && context.Request.Path != "/health")
    {
        var offered = context.Request.Headers.Authorization.ToString();
        var expected = $"Bearer {token}";
        if (!CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(offered), Encoding.UTF8.GetBytes(expected)))
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            await context.Response.WriteAsJsonAsync(new Problem("unauthorised"));
            return;
        }
    }
    await next(context);
});

app.MapGet("/health", () => new Health("ok", Calculator.OsuVersion, Calculator.RulesetNames));
app.MapPost("/v1/beatmap", (MapRequest request, Calculator calculator, CancellationToken cancellation)
    => Answer(() => calculator.Map(request, cancellation), cancellation));
app.MapPost("/v1/score", (ScoreRequest request, Calculator calculator, CancellationToken cancellation)
    => Answer(() => calculator.Score(request, cancellation), cancellation));
app.MapPost("/v1/whatif", (WhatIfRequest request, Calculator calculator, CancellationToken cancellation)
    => Answer(() => calculator.WhatIf(request, cancellation), cancellation));

app.Logger.LogInformation("assay: osu! {Version}, rulesets {Rulesets}", Calculator.OsuVersion, string.Join(", ", Calculator.RulesetNames));
await app.RunAsync();
return 0;

async Task<IResult> Answer<T>(Func<Task<T>> work, CancellationToken cancellation)
{
    await working.WaitAsync(cancellation);
    try
    {
        return Results.Ok(await work());
    }
    catch (BadRequest exception)
    {
        return Results.Json(new Problem("bad request", exception.Message), statusCode: StatusCodes.Status422UnprocessableEntity);
    }
    catch (ChecksumMismatch exception)
    {
        return Results.Json(new Problem("checksum mismatch", exception.Message), statusCode: StatusCodes.Status409Conflict);
    }
    catch (MapUnavailable exception)
    {
        return Results.Json(new Problem("map unavailable", exception.Message), statusCode: StatusCodes.Status502BadGateway);
    }
    finally
    {
        working.Release();
    }
}

internal static class Settings
{
    public static int Int(string name, int fallback)
        => int.TryParse(Environment.GetEnvironmentVariable(name), out int value) && value > 0 ? value : fallback;
}
