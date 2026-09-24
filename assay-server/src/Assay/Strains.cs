using osu.Game.Beatmaps;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Catch.Difficulty;
using osu.Game.Rulesets.Difficulty;
using osu.Game.Rulesets.Difficulty.Preprocessing;
using osu.Game.Rulesets.Difficulty.Skills;
using osu.Game.Rulesets.Mania.Difficulty;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Osu.Difficulty;
using osu.Game.Rulesets.Osu.Difficulty.Skills;
using osu.Game.Rulesets.Taiko.Difficulty;

namespace Assay;

/// <summary>
/// How hard each stretch of a map is: every 400 ms of played time gets the hardest object in it,
/// as the game's own skills rated that object during a difficulty calculation. The calculators
/// below only keep what the game hands them (the difficulty objects and the processed skills);
/// the numbers are the game's.
/// </summary>
internal static class Strains
{
    public const double SectionLength = 400;

    public static List<double> Sections(Ruleset ruleset, IWorkingBeatmap working, Mod[] mods)
    {
        IPeek peek = ruleset switch
        {
            osu.Game.Rulesets.Osu.OsuRuleset => new OsuPeek(ruleset.RulesetInfo, working),
            osu.Game.Rulesets.Taiko.TaikoRuleset => new TaikoPeek(ruleset.RulesetInfo, working),
            osu.Game.Rulesets.Catch.CatchRuleset => new CatchPeek(ruleset.RulesetInfo, working),
            osu.Game.Rulesets.Mania.ManiaRuleset => new ManiaPeek(ruleset.RulesetInfo, working),
            _ => throw new BadRequest($"no strains for {ruleset.ShortName}"),
        };
        ((DifficultyCalculator)peek).Calculate(mods);
        var objects = peek.Objects;
        var skills = peek.Skills;

        // osu!: aim (the one that counts sliders) and speed, the two a player feels; elsewhere every skill.
        var chosen = ruleset is osu.Game.Rulesets.Osu.OsuRuleset
            ? new Skill?[] { skills.FirstOrDefault(s => s is Aim), skills.FirstOrDefault(s => s is Speed) }.OfType<Skill>().ToList()
            : skills.ToList();
        if (chosen.Count == 0 || objects.Count == 0)
            return [];

        double first = objects[0].StartTime;
        int count = (int)((objects[^1].StartTime - first) / SectionLength) + 1;
        var sections = new double[count];
        foreach (var skill in chosen)
        {
            var rated = skill.GetObjectDifficulties();
            var peaks = new double[count];
            for (int i = 0; i < Math.Min(rated.Count, objects.Count); i++)
            {
                int at = (int)((objects[i].StartTime - first) / SectionLength);
                peaks[at] = Math.Max(peaks[at], Math.Max(0, rated[i]));
            }
            for (int i = 0; i < count; i++)
                sections[i] += peaks[i];
        }
        return sections.ToList();
    }

    /// <summary>Scaled to 0..1 and averaged down to at most <paramref name="points"/> values.</summary>
    public static List<double> Shape(List<double> sections, int points)
    {
        if (sections.Count == 0)
            return [];
        double top = sections.Max();
        if (top <= 0)
            top = 1;
        var scaled = sections.Select(v => v / top).ToList();
        if (scaled.Count <= points)
            return scaled.Select(v => Math.Round(v, 4)).ToList();

        var shaped = new List<double>(points);
        for (int i = 0; i < points; i++)
        {
            int from = i * scaled.Count / points;
            int to = Math.Max(from + 1, (i + 1) * scaled.Count / points);
            shaped.Add(Math.Round(scaled.Skip(from).Take(to - from).Average(), 4));
        }
        return shaped;
    }

    private interface IPeek
    {
        IReadOnlyList<DifficultyHitObject> Objects { get; }
        Skill[] Skills { get; }
    }

    private sealed class OsuPeek(IRulesetInfo ruleset, IWorkingBeatmap beatmap) : OsuDifficultyCalculator(ruleset, beatmap), IPeek
    {
        public IReadOnlyList<DifficultyHitObject> Objects { get; private set; } = [];
        public Skill[] Skills { get; private set; } = [];

        protected override IEnumerable<DifficultyHitObject> CreateDifficultyHitObjects(IBeatmap beatmap, Mod[] mods)
            => Objects = base.CreateDifficultyHitObjects(beatmap, mods).ToList();

        protected override DifficultyAttributes CreateDifficultyAttributes(IBeatmap beatmap, Mod[] mods, Skill[] skills)
        {
            Skills = skills;
            return base.CreateDifficultyAttributes(beatmap, mods, skills);
        }
    }

    private sealed class TaikoPeek(IRulesetInfo ruleset, IWorkingBeatmap beatmap) : TaikoDifficultyCalculator(ruleset, beatmap), IPeek
    {
        public IReadOnlyList<DifficultyHitObject> Objects { get; private set; } = [];
        public Skill[] Skills { get; private set; } = [];

        protected override IEnumerable<DifficultyHitObject> CreateDifficultyHitObjects(IBeatmap beatmap, Mod[] mods)
            => Objects = base.CreateDifficultyHitObjects(beatmap, mods).ToList();

        protected override DifficultyAttributes CreateDifficultyAttributes(IBeatmap beatmap, Mod[] mods, Skill[] skills)
        {
            Skills = skills;
            return base.CreateDifficultyAttributes(beatmap, mods, skills);
        }
    }

    private sealed class CatchPeek(IRulesetInfo ruleset, IWorkingBeatmap beatmap) : CatchDifficultyCalculator(ruleset, beatmap), IPeek
    {
        public IReadOnlyList<DifficultyHitObject> Objects { get; private set; } = [];
        public Skill[] Skills { get; private set; } = [];

        protected override IEnumerable<DifficultyHitObject> CreateDifficultyHitObjects(IBeatmap beatmap, Mod[] mods)
            => Objects = base.CreateDifficultyHitObjects(beatmap, mods).ToList();

        protected override DifficultyAttributes CreateDifficultyAttributes(IBeatmap beatmap, Mod[] mods, Skill[] skills)
        {
            Skills = skills;
            return base.CreateDifficultyAttributes(beatmap, mods, skills);
        }
    }

    private sealed class ManiaPeek(IRulesetInfo ruleset, IWorkingBeatmap beatmap) : ManiaDifficultyCalculator(ruleset, beatmap), IPeek
    {
        public IReadOnlyList<DifficultyHitObject> Objects { get; private set; } = [];
        public Skill[] Skills { get; private set; } = [];

        protected override IEnumerable<DifficultyHitObject> CreateDifficultyHitObjects(IBeatmap beatmap, Mod[] mods)
            => Objects = base.CreateDifficultyHitObjects(beatmap, mods).ToList();

        protected override DifficultyAttributes CreateDifficultyAttributes(IBeatmap beatmap, Mod[] mods, Skill[] skills)
        {
            Skills = skills;
            return base.CreateDifficultyAttributes(beatmap, mods, skills);
        }
    }
}
