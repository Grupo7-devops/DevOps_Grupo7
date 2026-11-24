#!/usr/bin/env python3
"""
Script de Pruebas de Stress y Generación de Eventos para New Relic (Entrega 4 - Grupo 7)
MISW-4304 DevOps: Agilizando el Despliegue Continuo de Aplicaciones - Universidad de los Andes

No requiere dependencias externas (usa solo librería estándar de Python 3).
Genera tokens HS256 compatibles con Flask-JWT-Extended automáticamente a partir de --jwt-secret,
o acepta un token existente con --token.

Ejemplos de uso:
  # Prueba rápida (desarrollo local - 30 segundos)
  python3 stress_test_entrega4.py --base-url http://localhost:8000 --jwt-secret "mi-clave-secreta" --mode quick

  # Prueba completa de stress en AWS Fargate (~5-6 minutos, 4 fases)
  python3 stress_test_entrega4.py --base-url http://<ALB_DNS> --jwt-secret "super-secret-jwt-key-grupo7" --mode full

  # Usando un JWT ya generado
  python3 stress_test_entrega4.py --base-url http://<ALB_DNS> --token "<TU_JWT>" --mode full
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_jwt(secret: str, identity: str = "blacklist-client") -> str:
    """Genera un JWT HS256 compatible con Flask-JWT-Extended 3.25.1."""
    now = int(time.time())
    header = {"typ": "JWT", "alg": "HS256"}
    payload = {
        "iat": now,
        "nbf": now,
        "jti": str(uuid.uuid4()),
        "identity": identity,
        "fresh": False,
        "type": "access",
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def http_request(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 10.0):
    data = None
    req_headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _ = resp.read()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return resp.status, elapsed_ms
    except urllib.error.HTTPError as e:
        _ = e.read()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return e.code, elapsed_ms
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return 0, elapsed_ms


def run_phase(
    name: str,
    duration_sec: int,
    workers: int,
    task_fn,
    pause_between_req: float = 0.0,
):
    print(f"\n{'=' * 74}")
    print(f"FASE: {name}")
    print(f"Duración: {duration_sec}s | Concurrencia: {workers} hilos | Pausa: {pause_between_req}s")
    print(f"{'=' * 74}")

    end_time = time.time() + duration_sec
    latencies = []
    status_counts = Counter()
    endpoint_counts = Counter()
    t0 = time.perf_counter()

    def worker_loop(worker_id: int):
        local_lat = []
        local_status = Counter()
        local_ep = Counter()
        i = 0
        while time.time() < end_time:
            ep_label, status, elapsed_ms = task_fn(worker_id, i)
            local_lat.append(elapsed_ms)
            local_status[status] += 1
            local_ep[ep_label] += 1
            i += 1
            if pause_between_req > 0:
                time.sleep(pause_between_req)
        return local_lat, local_status, local_ep

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker_loop, w) for w in range(workers)]
        for f in as_completed(futures):
            l_lat, l_st, l_ep = f.result()
            latencies.extend(l_lat)
            status_counts.update(l_st)
            endpoint_counts.update(l_ep)

    total_time = max(time.perf_counter() - t0, 0.001)
    total_reqs = len(latencies)
    rps = total_reqs / total_time

    if latencies:
        lat_sorted = sorted(latencies)
        avg_ms = statistics.mean(lat_sorted)
        p50_ms = lat_sorted[int(0.50 * (total_reqs - 1))]
        p95_ms = lat_sorted[int(0.95 * (total_reqs - 1))]
        p99_ms = lat_sorted[int(0.99 * (total_reqs - 1))]
        max_ms = lat_sorted[-1]
    else:
        avg_ms = p50_ms = p95_ms = p99_ms = max_ms = 0.0

    errors_4xx_5xx = sum(cnt for code, cnt in status_counts.items() if code == 0 or code >= 400)
    error_pct = (errors_4xx_5xx / total_reqs * 100.0) if total_reqs else 0.0

    print(f"Total requests : {total_reqs} ({rps:.2f} req/s | {rps * 60:.0f} rpm)")
    print(f"Latencia (ms)  : avg={avg_ms:.1f} | p50={p50_ms:.1f} | p95={p95_ms:.1f} | p99={p99_ms:.1f} | max={max_ms:.1f}")
    print(f"Códigos HTTP   : {dict(sorted(status_counts.items()))}")
    print(f"Tasa de error  : {error_pct:.2f}% ({errors_4xx_5xx}/{total_reqs})")
    print(f"Por endpoint   : {dict(endpoint_counts)}")

    return {
        "phase": name,
        "total_reqs": total_reqs,
        "rps": round(rps, 2),
        "rpm": round(rps * 60, 1),
        "avg_ms": round(avg_ms, 2),
        "p50_ms": round(p50_ms, 2),
        "p95_ms": round(p95_ms, 2),
        "p99_ms": round(p99_ms, 2),
        "max_ms": round(max_ms, 2),
        "error_pct": round(error_pct, 2),
        "status_counts": dict(sorted(status_counts.items())),
    }


def main():
    parser = argparse.ArgumentParser(description="Pruebas de stress y observabilidad New Relic - Grupo 7")
    parser.add_argument("--base-url", required=True, help="URL base del servicio (ej. http://localhost:8000 o http://<ALB_DNS>)")
    parser.add_argument("--jwt-secret", default=None, help="JWT_SECRET_KEY del servidor para generar token automáticamente")
    parser.add_argument("--token", default=None, help="Bearer JWT ya generado (alternativa a --jwt-secret)")
    parser.add_argument("--mode", choices=["quick", "full", "errors-only"], default="full", help="Perfil de ejecución")
    parser.add_argument("--stress-workers", type=int, default=25, help="Hilos concurrentes en la fase de stress (default: 25)")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if args.token:
        token = args.token.strip()
    elif args.jwt_secret:
        token = generate_jwt(args.jwt_secret)
    else:
        sys.exit("Error: debes indicar --jwt-secret o --token.")

    auth_headers = {"Authorization": f"Bearer {token}"}
    invalid_auth_headers = {"Authorization": "Bearer token-invalido-prueba-newrelic"}
    run_id = uuid.uuid4().hex[:6]
    app_uuid = "123e4567-e89b-12d3-a456-426614174000"
    seeded_emails = []

    print(f"Verificando conectividad con {base_url}/health ...")
    st, ms = http_request("GET", f"{base_url}/health", {})
    if st != 200:
        sys.exit(f"Error: /health respondió HTTP {st} ({ms:.1f} ms). Verifica que el servicio esté activo.")
    print(f"OK: /health respondió HTTP 200 en {ms:.1f} ms")

    # Sembrar 5 emails iniciales para consultas GET reales en DB
    for idx in range(5):
        email = f"seed-{run_id}-{idx}@uniandes.edu.co"
        code, _ = http_request(
            "POST",
            f"{base_url}/blacklists",
            auth_headers,
            {"email": email, "app_uuid": app_uuid, "blocked_reason": f"Semilla stress {idx}"},
        )
        if code in (201, 409):
            seeded_emails.append(email)
    print(f"Sembrados {len(seeded_emails)} registros iniciales en base de datos.")

    def make_normal_traffic(phase_tag: str):
        def normal_traffic(worker_id: int, seq: int):
            r = random.random()
            if r < 0.25:
                code, ms_ = http_request("GET", f"{base_url}/health", {})
                return "GET /health", code, ms_
            elif r < 0.65 and seeded_emails:
                email = random.choice(seeded_emails)
                code, ms_ = http_request("GET", f"{base_url}/blacklists/{email}", auth_headers)
                return "GET /blacklists/{email}", code, ms_
            elif r < 0.85:
                email = f"libre-{run_id}-{phase_tag}-{worker_id}-{seq}@correo.com"
                code, ms_ = http_request("GET", f"{base_url}/blacklists/{email}", auth_headers)
                return "GET /blacklists/{not_found}", code, ms_
            else:
                email = f"nr-{run_id}-{phase_tag}-{worker_id}-{seq}@stress.test"
                code, ms_ = http_request(
                    "POST",
                    f"{base_url}/blacklists",
                    auth_headers,
                    {"email": email, "app_uuid": app_uuid, "blocked_reason": "Prueba carga New Relic"},
                )
                if code == 201 and len(seeded_emails) < 200:
                    seeded_emails.append(email)
                return "POST /blacklists", code, ms_
        return normal_traffic

    def error_wave_traffic(worker_id: int, seq: int):
        r = random.random()
        if r < 0.25:
            # 401 Unauthorized (sin token)
            code, ms_ = http_request("GET", f"{base_url}/blacklists/sin-token@test.com", {})
            return "ERR 401 sin token", code, ms_
        elif r < 0.50:
            # 403 Forbidden (token inválido)
            code, ms_ = http_request("GET", f"{base_url}/blacklists/token-malo@test.com", invalid_auth_headers)
            return "ERR 403 token invalido", code, ms_
        elif r < 0.75:
            # 400 Bad Request (UUID y email inválidos)
            code, ms_ = http_request(
                "POST",
                f"{base_url}/blacklists",
                auth_headers,
                {"email": "correo-invalido", "app_uuid": "no-es-uuid", "blocked_reason": "x" * 300},
            )
            return "ERR 400 payload invalido", code, ms_
        else:
            # 409 Conflict (email duplicado)
            dup_email = seeded_emails[0] if seeded_emails else f"dup-{run_id}@test.com"
            code, ms_ = http_request(
                "POST",
                f"{base_url}/blacklists",
                auth_headers,
                {"email": dup_email, "app_uuid": app_uuid, "blocked_reason": "Intento duplicado"},
            )
            return "ERR 409 duplicado", code, ms_

    summary = []
    if args.mode == "quick":
        summary.append(run_phase("1. Tráfico normal local (Dev)", duration_sec=20, workers=4, task_fn=make_normal_traffic("q1"), pause_between_req=0.1))
        summary.append(run_phase("2. Errores controlados local (Dev)", duration_sec=10, workers=2, task_fn=error_wave_traffic, pause_between_req=0.2))
    elif args.mode == "errors-only":
        summary.append(run_phase("Oleada de errores controlados (400/401/403/409)", duration_sec=90, workers=8, task_fn=error_wave_traffic, pause_between_req=0.05))
    else:
        summary.append(run_phase("1. Línea Base (Carga Normal)", duration_sec=60, workers=3, task_fn=make_normal_traffic("p1"), pause_between_req=0.15))
        summary.append(run_phase(f"2. Stress Sostenido (Alta Concurrencia - {args.stress_workers} hilos)", duration_sec=120, workers=args.stress_workers, task_fn=make_normal_traffic("p2"), pause_between_req=0.01))
        summary.append(run_phase("3. Oleada de Errores de Aplicación (400/401/403/409)", duration_sec=90, workers=8, task_fn=error_wave_traffic, pause_between_req=0.05))
        summary.append(run_phase("4. Recuperación (Carga Normal Post-Stress)", duration_sec=60, workers=3, task_fn=make_normal_traffic("p4"), pause_between_req=0.15))

    print("\n" + "=" * 74)
    print("RESUMEN CONSOLIDADO (Copia esta tabla en tu Documento de Entrega 4)")
    print("=" * 74)
    print(f"| {'Fase':<48} | {'Reqs':>6} | {'RPS':>6} | {'Avg(ms)':>7} | {'P95(ms)':>7} | {'Err%':>6} |")
    print(f"|{'-'*50}|{'-'*8}|{'-'*8}|{'-'*9}|{'-'*9}|{'-'*8}|")
    for s in summary:
        print(
            f"| {s['phase']:<48} | {s['total_reqs']:>6} | {s['rps']:>6.1f} | "
            f"{s['avg_ms']:>7.1f} | {s['p95_ms']:>7.1f} | {s['error_pct']:>5.1f}% |"
        )
    print("\nListo. Espera 1-2 minutos y revisa New Relic (APM, Databases, Apdex, Errors Inbox y Alerts).")


if __name__ == "__main__":
    main()
