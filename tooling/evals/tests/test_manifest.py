from evals.manifest import build_manifest, read_manifest, write_manifest


def test_manifest_round_trip_has_reproducibility_fields(tmp_path):
    m = build_manifest(
        run_label="x", model_alias="a", prompt_version="p1", judge_model="j"
    )
    write_manifest(m, tmp_path / "m.json")
    back = read_manifest(tmp_path / "m.json")
    assert back == m
    assert back.dataset_version == "v1" and len(back.dataset_sha256) == 64
    assert (
        back.capture_schema == "capture.v1" and back.thresholds.max_pass_rate_drop > 0
    )
