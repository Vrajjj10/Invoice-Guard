def test_upload_processes_and_caches(client, sample_files):
    data = sample_files["digital"].read_bytes()

    r1 = client.post("/invoices/upload", files={"file": ("inv.pdf", data, "application/pdf")})
    assert r1.status_code == 202
    body = r1.json()
    assert body["cached"] is False

    # TestClient runs background tasks before returning, so the job is finished here
    job = client.get(f"/jobs/{body['job_id']}").json()
    assert job["status"] == "done"
    assert job["extraction_method"] == "text"
    assert "SGT/2026/0457" in job["extracted_text"]

    r2 = client.post("/invoices/upload", files={"file": ("copy.pdf", data, "application/pdf")})
    assert r2.json() == {**body, "cached": True, "status": "done"}


def test_upload_rejects_unsupported_type(client):
    r = client.post("/invoices/upload", files={"file": ("x.txt", b"not an invoice", "text/plain")})
    assert r.status_code == 415


def test_unknown_job_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404
