//! f3d2sc-decode: decode a Fusion .f3d with cadmpeg into its neutral IR (JSON) and,
//! optionally, write the final B-rep as STEP AP214 for reference measurements.
use cadmpeg_codec_f3d::F3dCodec;
use cadmpeg_codec_step::StepCodec;
use cadmpeg_ir::codec::write::{target::TargetRequest, EncodeInput, Encoder};
use cadmpeg_ir::{Codec, DecodeOptions};
use std::process::ExitCode;

/// Write the archive's design streams (everything but the B-rep blobs and previews) under `dir`,
/// uncompressed, keeping their archive paths. The converter reads parameters cadmpeg leaves
/// native from them.
fn extract_streams(bytes: &[u8], dir: &std::path::Path) -> Result<(), String> {
    use cadmpeg_core::decode::{DecodeArena, DecodeContext, DecodePolicy, ResourceLimits};
    let arena = DecodeArena::new();
    let policy = DecodePolicy { limits: ResourceLimits::service() };
    let (ctx, root) = DecodeContext::from_root_bytes(bytes, &arena, &policy).map_err(|e| e.to_string())?;
    let snap = cadmpeg_container::ArchiveSnapshot::new(&ctx, root).map_err(|e| e.to_string())?;
    let names: Vec<String> = snap.entries().iter().map(|e| e.name.clone()).collect();
    for name in names {
        let keep = name.ends_with(".dat") || name.ends_with(".protein");
        if !keep || name.contains("..") || name.starts_with('/') {
            continue;
        }
        let view = snap.open(&ctx, &name).map_err(|e| format!("{name}: {e}"))?;
        let out = dir.join(&name);
        if let Some(parent) = out.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        std::fs::write(&out, view.window()).map_err(|e| format!("{}: {e}", out.display()))?;
    }
    Ok(())
}

/// `--entry <in.f3d> <name> <out>`: write one archive entry, decompressed.
fn extract_entry(a: &[String]) -> Result<(), String> {
    use cadmpeg_core::decode::{DecodeArena, DecodeContext, DecodePolicy, ResourceLimits};
    let (path, name, out) = match a {
        [_, _, p, n, o, ..] => (p, n, o),
        _ => return Err("usage: f3d2sc-decode --entry <in.f3d> <entry name> <out>".into()),
    };
    let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    let arena = DecodeArena::new();
    let policy = DecodePolicy { limits: ResourceLimits::service() };
    let (ctx, root) = DecodeContext::from_root_bytes(&bytes, &arena, &policy).map_err(|e| e.to_string())?;
    let snap = cadmpeg_container::ArchiveSnapshot::new(&ctx, root).map_err(|e| e.to_string())?;
    let view = snap.open(&ctx, name).map_err(|e| format!("{name}: {e}"))?;
    std::fs::write(out, view.window()).map_err(|e| format!("{out}: {e}"))
}

fn run() -> Result<(), String> {
    let a: Vec<String> = std::env::args().collect();
    if a.get(1).map(String::as_str) == Some("--version") {
        println!("f3d2sc-decode cadmpeg {}", env!("CADMPEG_COMMIT"));
        return Ok(());
    }
    if a.get(1).map(String::as_str) == Some("--entry") {
        return extract_entry(&a);
    }
    if a.len() < 3 {
        return Err("usage: f3d2sc-decode <in.f3d> <out.ir.json> [out.step [body indices, 0-based: 0,2]] (also writes <out.ir.json>.streams/)".into());
    }
    let bytes = std::fs::read(&a[1]).map_err(|e| format!("{}: {e}", a[1]))?;
    let r = F3dCodec.decode(&mut std::io::Cursor::new(&bytes), &DecodeOptions::default()).map_err(|e| e.to_string())?;
    let streams = std::path::PathBuf::from(format!("{}.streams", a[2]));
    extract_streams(&bytes, &streams)?;
    let losses: Vec<_> = r
        .report()
        .losses
        .iter()
        .map(|l| serde_json::json!({"severity": format!("{:?}", l.severity), "message": l.message}))
        .collect();
    let mut ir = serde_json::to_value(r.ir()).map_err(|e| e.to_string())?;
    if let Some(o) = ir.as_object_mut() {
        o.insert("decode_losses".into(), serde_json::Value::Array(losses));
        // The CADIR v11 wire leaves out the arenas the converter reads (sketches, their
        // entities and constraints, ...); the decoded model still holds them.
        let omitted = r.ir().model.omitted_arenas_value().map_err(|e| e.to_string())?;
        o.insert("wire_omitted".into(), omitted);
    }
    std::fs::write(&a[2], serde_json::to_vec(&ir).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    if let Some(step) = a.get(3) {
        // Optional fifth argument: the 0-based indices of the bodies to write ("2" or "0,3").
        let ir = match a.get(4) {
            Some(sel) => {
                let keep: Vec<usize> = sel.split(',').filter_map(|x| x.trim().parse().ok()).collect();
                keep_bodies(r.ir(), &keep)?
            }
            None => r.ir().clone(),
        };
        let plan = StepCodec::default()
            .plan(EncodeInput::new(&ir, None), TargetRequest::Explicit("ap214"))
            .or_else(|_| StepCodec::default().plan(EncodeInput::new(&ir, None), TargetRequest::Inherit))
            .map_err(|e| format!("STEP plan: {e}"))?;
        let mut out = std::fs::File::create(step).map_err(|e| e.to_string())?;
        let rep = plan.write_to(&mut out).map_err(|e| format!("STEP write: {e}"))?;
        for l in &rep.losses {
            eprintln!("step: {:?}: {}", l.severity, l.message);
        }
    }
    Ok(())
}

/// The document with only the bodies at `keep` (0-based) and the topology they own.
fn keep_bodies(ir: &cadmpeg_ir::CadIr, keep: &[usize]) -> Result<cadmpeg_ir::CadIr, String> {
    use serde_json::Value;
    use std::collections::HashSet;
    let mut v = serde_json::to_value(ir).map_err(|e| e.to_string())?;
    let model = v.get_mut("model").and_then(Value::as_object_mut).ok_or("no model")?;
    let ids = |arr: &Value| -> Vec<String> {
        arr.as_array().into_iter().flatten().filter_map(|x| x.get("id").and_then(Value::as_str).map(str::to_string)).collect()
    };
    let s = |x: &Value, k: &str| x.get(k).and_then(Value::as_str).map(str::to_string);
    let bodies: Vec<Value> = model.get("bodies").and_then(Value::as_array).cloned().unwrap_or_default();
    let kept_bodies: HashSet<String> = bodies.iter().enumerate().filter(|(i, _)| keep.contains(i)).filter_map(|(_, b)| s(b, "id")).collect();
    let mut drop: HashSet<String> = ids(&Value::Array(bodies)).into_iter().filter(|i| !kept_bodies.contains(i)).collect();
    let regions = model.get("regions").cloned().unwrap_or(Value::Null);
    let mut kept_shells = HashSet::new();
    for r in regions.as_array().into_iter().flatten() {
        let mine = s(r, "body").is_some_and(|b| kept_bodies.contains(&b));
        let shells: Vec<String> = r.get("shells").and_then(Value::as_array).into_iter().flatten().filter_map(|x| x.as_str().map(str::to_string)).collect();
        if mine {
            kept_shells.extend(shells);
        } else {
            drop.extend(s(r, "id"));
            drop.extend(shells);
        }
    }
    let faces = model.get("faces").cloned().unwrap_or(Value::Null);
    for f in faces.as_array().into_iter().flatten() {
        if !s(f, "shell").is_some_and(|sh| kept_shells.contains(&sh)) {
            drop.extend(s(f, "id"));
            for l in f.get("loops").and_then(|l| l.get("loops")).and_then(Value::as_array).into_iter().flatten() {
                drop.extend(l.as_str().map(str::to_string));
            }
        }
    }
    let coedges = model.get("coedges").cloned().unwrap_or(Value::Null);
    let mut kept_edges = HashSet::new();
    for c in coedges.as_array().into_iter().flatten() {
        if s(c, "owner_loop").is_some_and(|l| drop.contains(&l)) {
            drop.extend(s(c, "id"));
        } else {
            kept_edges.extend(s(c, "edge"));
        }
    }
    let edges = model.get("edges").cloned().unwrap_or(Value::Null);
    let mut kept_vertices = HashSet::new();
    for e in edges.as_array().into_iter().flatten() {
        if s(e, "id").is_some_and(|i| kept_edges.contains(&i)) {
            kept_vertices.extend(s(e, "start"));
            kept_vertices.extend(s(e, "end"));
        } else {
            drop.extend(s(e, "id"));
        }
    }
    let vertices = model.get("vertices").cloned().unwrap_or(Value::Null);
    for x in vertices.as_array().into_iter().flatten() {
        if !s(x, "id").is_some_and(|i| kept_vertices.contains(&i)) {
            drop.extend(s(x, "id"));
        }
    }
    // A subset is written as loose bodies: no assembly structure (occurrences, joints, product
    // definitions), so the STEP holds just the bodies.
    for arena in ["occurrences", "joints", "assembly_joints", "product_definitions"] {
        if let Some(arr) = model.get_mut(arena).and_then(Value::as_array_mut) {
            arr.clear();
        }
    }
    // Remove the dropped entities from every model arena, and records that name them.
    let refs_dropped = |x: &Value| -> bool {
        let t = x.to_string();
        drop.iter().any(|d| t.contains(&format!("\"{d}\"")))
    };
    for (arena, val) in model.iter_mut() {
        if let Some(arr) = val.as_array_mut() {
            let topo = matches!(arena.as_str(), "bodies" | "regions" | "shells" | "faces" | "loops" | "coedges" | "edges" | "vertices");
            arr.retain(|x| {
                let own = x.get("id").and_then(Value::as_str).is_some_and(|i| drop.contains(i));
                if topo { !own } else { !own && !refs_dropped(x) }
            });
        }
    }
    serde_json::from_value(v).map_err(|e| format!("filtered document: {e}"))
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("f3d2sc-decode: {e}");
            ExitCode::FAILURE
        }
    }
}
