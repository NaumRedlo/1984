//! Where the speed figure disagrees with ppy, grouped by mods.
use std::collections::BTreeMap;
use dossier_beatmap::Beatmap;
use dossier_replay::{bits, Mods};

fn mods_of(key: &str) -> Option<Mods> {
    if key == "NM" { return Some(Mods::new(0)); }
    let mut raw = 0u32;
    for pair in key.as_bytes().chunks(2) {
        raw |= match std::str::from_utf8(pair).ok()? {
            "NF" => bits::NO_FAIL, "EZ" => bits::EASY, "TD" => bits::TOUCH_DEVICE,
            "HD" => bits::HIDDEN, "HR" => bits::HARD_ROCK, "DT" => bits::DOUBLE_TIME,
            "HT" => bits::HALF_TIME, "NC" => bits::NIGHTCORE | bits::DOUBLE_TIME,
            "FL" => bits::FLASHLIGHT, _ => return None,
        };
    }
    Some(Mods::new(raw))
}

fn main() {
    let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("corpus");
    let text = std::fs::read_to_string(dir.join("expected.json")).unwrap();
    let corpus: serde_json::Value = serde_json::from_str(&text).unwrap();
    let mut by_mods: BTreeMap<String, Vec<f64>> = BTreeMap::new();

    for entry in corpus["maps"].as_array().unwrap() {
        let id = entry["beatmap_id"].as_u64().unwrap();
        let map = Beatmap::parse(&std::fs::read_to_string(dir.join("maps").join(format!("{id}.osu"))).unwrap()).unwrap();
        for (key, attrs) in entry["attributes"].as_object().unwrap() {
            let Some(mods) = mods_of(key) else { continue };
            let field = std::env::args().nth(1).unwrap_or_else(|| "speed_difficulty".into());
            let Some(theirs) = attrs[field.as_str()].as_f64() else { continue };
            let field = std::env::args().nth(1).unwrap_or_else(|| "speed_difficulty".into());
            let ours = match field.as_str() {
                "aim_difficulty" => dossier_assay::aim_difficulty(&map, mods).0,
                "slider_factor" => dossier_assay::aim_difficulty(&map, mods).1,
                _ => dossier_assay::speed_difficulty(&map, mods),
            };
            by_mods.entry(key.clone()).or_default().push((ours - theirs) / theirs * 100.0);
        }
    }
    println!("{:8} {:>9} {:>9} {:>9}", "моды", "средн%", "мин%", "макс%");
    for (key, offs) in &by_mods {
        let mean = offs.iter().sum::<f64>() / offs.len() as f64;
        let min = offs.iter().cloned().fold(f64::INFINITY, f64::min);
        let max = offs.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        println!("{key:8} {mean:9.2} {min:9.2} {max:9.2}");
    }
}
