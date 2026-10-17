"""Shared finite budgets and killable operations, including Windows spawn workers."""

from __future__ import annotations

import multiprocessing
import time
from collections import Counter


class BudgetExhausted(RuntimeError):
    def __init__(self, reason: str, scope: str = "organisation"):
        self.reason, self.scope = reason, scope
        super().__init__(f"{scope}:{reason}")


def _execute(pipe, function, args):
    try:
        pipe.send((True, function(*args)))
    except BaseException as error:
        pipe.send((False, f"{type(error).__name__}: {error}"))
    finally:
        pipe.close()


def bounded_call(function, args, seconds: float, *, check=None):
    """Run an operation in an owned process; terminate it at its absolute deadline.

    Real HTTP/SDK calls, DNS and PDF parsers are isolated. Test transports may be
    injected inline; production operations cannot outlive the owning deadline.
    """
    if seconds <= 0:
        raise BudgetExhausted("elapsed_time", "run")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_execute, args=(child, function, args))
    process.start()
    child.close()
    try:
        end = time.monotonic() + seconds
        while not parent.poll(min(0.1, max(0, end - time.monotonic()))):
            if check:
                check()
            if time.monotonic() >= end:
                raise TimeoutError("Operation deadline exceeded")
        if check:
            check()
        try:
            ok, result = parent.recv()
        except EOFError as error:
            raise RuntimeError("Operation worker exited without a result") from error
        if not ok:
            raise RuntimeError(result)
        return result
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)


class Budget:
    def __init__(
        self, limits, *, clock=time.monotonic, sleep=time.sleep, audit=None, cancel_file=None
    ):
        self.limits, self.clock, self.sleep, self.audit = limits, clock, sleep, audit
        self.cancel_file = cancel_file
        self.started = clock()
        self.org_started = self.started
        self.organisation = None
        self.model_caps = {}
        self.total = Counter()
        self.current = Counter()

    def begin(self, organisation):
        self.organisation = organisation
        self.org_started = self.clock()
        self.current = Counter()
        self.publish()

    def remaining(self):
        return max(
            0.0,
            min(
                self.limits.run_seconds - (self.clock() - self.started),
                self.limits.organisation_seconds - (self.clock() - self.org_started)
                if self.organisation is not None
                else float("inf"),
            ),
        )

    def check_run(self):
        if self.cancel_file and self.cancel_file.exists():
            raise BudgetExhausted("cancelled", "run")
        if self.clock() - self.started >= self.limits.run_seconds:
            raise BudgetExhausted("elapsed_time", "run")

    def check(self):
        self.check_run()
        if self.organisation is not None:
            if self.clock() - self.org_started >= self.limits.organisation_seconds:
                raise BudgetExhausted("elapsed_time")

    def timeout(self, configured):
        self.check()
        return min(float(configured), self.remaining())

    def wait(self, seconds):
        self.check()
        limited = seconds >= self.remaining()
        delay = min(seconds, self.remaining())
        while delay > 0:
            step = min(0.1, delay)
            self.sleep(step)
            delay -= step
            self.check()
        if limited:
            raise BudgetExhausted("elapsed_time")

    def request(self, kind):
        self.check()
        if self.total["requests"] >= self.limits.run_requests:
            raise BudgetExhausted("request_limit", "run")
        if self.current["requests"] >= self.limits.organisation_requests:
            raise BudgetExhausted("request_limit")
        self.count("requests")
        self.count(kind + "_requests")

    def count(self, key, number=1):
        self.total[key] += number
        self.current[key] += number
        self.publish()

    def snapshot(self):
        return {
            "model_caps": self.model_caps,
            "remaining_model_calls": max(
                0,
                self.model_caps.get("total", 0)
                - self.total["local_model_requests"]
                - self.total["paid_model_requests"],
            ),
            "limits": self.limits.model_dump(),
            "used": dict(self.total),
            "organisation_used": dict(self.current),
            "organisation_id": self.organisation,
            "remaining_requests": max(0, self.limits.run_requests - self.total["requests"]),
            "organisation_remaining_requests": max(
                0, self.limits.organisation_requests - self.current["requests"]
            ),
            "remaining_seconds": round(self.remaining(), 2),
        }

    def publish(self):
        if self.audit:
            from .storage import write_json

            write_json(self.audit.path.parent / "budget.json", self.snapshot())
