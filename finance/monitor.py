"""Local recurring checks: no external notification delivery and no broker access."""
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .analytics import analyze
from .core import ValidationError
from .market import collect_quotes, merge_quotes, revalue
from .reporting import atomic_write, save_reports
from .storage import Store


def run_once(store, *, update=False, mode="daily", fetcher=None, use_cache=False):
    state=store.read()
    now=datetime.now(timezone.utc).isoformat()
    error=None
    quotes=None
    if update:
        try:
            if state["market"]["provider"]=="yahoo" and not use_cache:
                quotes=collect_quotes(state,**({"fetcher":fetcher} if fetcher else {}))
            candidate=Store.normalize(state)
            if quotes is not None:
                candidate["quotes"]=merge_quotes(candidate["quotes"],quotes)
            revalue(candidate)
            analyze(candidate)
        except ValidationError as exc:
            error=str(exc)
    def mutate(latest):
        if update and not error:
            for key in ["quotes","holdings","automatic_history","automatic_baseline","automatic_opening"]:
                latest[key]=candidate[key]
            latest["monitor"]["last_success"]=now
        if update:
            latest["monitor"]["last_error"]=error
        latest["monitor"]["last_run"]=now
        analysis=analyze(latest)
        signatures=[]
        previous=set(latest["monitor"]["signatures"])
        for alert in analysis["alerts"]:
            signature=hashlib.sha256(json.dumps([alert["code"],alert["title"],alert.get("stage"),alert["level"]],ensure_ascii=False).encode()).hexdigest()
            signatures.append(signature)
            if signature not in previous:
                latest["monitor"]["notices"].append({"time":now,"code":alert["code"],"title":alert["title"],"level":alert["level"]})
        latest["monitor"]["notices"]=latest["monitor"]["notices"][-100:]
        latest["monitor"]["signatures"]=signatures
    saved=store.change(state["revision"],mutate)
    result=save_reports(store.directory,saved,mode)
    return result


def due_modes(now,completed):
    local=now.astimezone(ZoneInfo("Europe/Berlin"))
    day=local.date().isoformat()
    modes=[]
    if local.weekday()<5 and (local.hour,local.minute)>=(22,15) and completed.get("daily")!=day:
        modes.append("daily")
    if local.weekday()==6 and (local.hour,local.minute)>=(18,0) and completed.get("weekly")!=day:
        modes.append("weekly")
    return modes


def scheduler_status(directory):
    try:
        data=json.loads((Path(directory)/"scheduler.json").read_text())
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(data["heartbeat"])).total_seconds()
        running=0<=age<90
    except (OSError,ValueError,KeyError):
        running=False
    return {"running":running,"timezone":"Europe/Berlin","daily":"Mo–Fr 22:15","weekly":"Sonntag 18:00"}


def daemon(store):
    # A single local daemon per data directory. Unix is the supported server platform.
    import fcntl
    with open(store.directory/"scheduler.lock","a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValidationError("Für dieses Datenverzeichnis läuft bereits ein Scheduler.") from None
        print("Depotblick-Zeitplan aktiv: Mo–Fr 22:15, Sonntag 18:00, Europe/Berlin. Hinweise nur im Dashboard.",flush=True)
        failed_attempts = {}
        while True:
            now=datetime.now(timezone.utc)
            atomic_write(store.directory/"scheduler.json",json.dumps({"heartbeat":now.isoformat(),"pid":os.getpid()}))
            state=store.read()
            for mode in due_modes(now,{**state.get("schedule_completed",{}), **failed_attempts}):
                day=now.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()
                backup=store.directory/"backups"/f"{day}.sqlite3"
                try:
                    if not backup.exists():
                        store.backup(backup)
                    run_once(store,update=mode=="daily",mode=mode)
                    latest=store.read()
                    store.change(latest["revision"],lambda s:s.setdefault("schedule_completed",{}).update({mode:day}))
                except Exception as exc:
                    failed_attempts[mode] = day
                    # No holdings or credentials in operational logs.
                    print(f"Geplanter Lauf fehlgeschlagen: {type(exc).__name__}",flush=True)
                    try:
                        latest=store.read()
                        def mark_failed(s):
                            s.setdefault("schedule_completed",{})[mode]=day
                            s["monitor"]["last_error"]="Geplanter Lauf technisch fehlgeschlagen. Manuellen Prüflauf starten; an diesem Tag keine automatischen Wiederholungen."
                        store.change(latest["revision"],mark_failed)
                    except Exception:
                        pass
            time.sleep(30)


def main():
    parser=argparse.ArgumentParser(description="Depotblick: bewerten, prüfen und lokale Berichte schreiben")
    parser.add_argument("--data-dir",type=Path,default=Path(__file__).resolve().parent.parent/".data")
    parser.add_argument("--daemon",action="store_true")
    parser.add_argument("--update",action="store_true")
    parser.add_argument("--mode",choices=["daily","weekly"],default="daily")
    args=parser.parse_args()
    store=Store(args.data_dir)
    try:
        if args.daemon:
            daemon(store)
        else:
            result=run_once(store,update=args.update,mode=args.mode)
            print(result["decision"]["label"]+" · Berichte lokal gespeichert.")
            raise SystemExit(1 if result["monitor"].get("last_error") else 2 if result["alerts"] else 0)
    except ValidationError as exc:
        parser.exit(1,str(exc)+"\n")
    except KeyboardInterrupt:
        pass


if __name__=="__main__":
    main()
