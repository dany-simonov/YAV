"""Stateful transaction model used to verify BE-04 quota invariants."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, RLock


@dataclass
class Reservation:
    state: str = "reserved"


class FakeTransactions:
    """Commit-time atomic model: workers may race, commits are serialized."""
    def __init__(self, limit: int):
        self.limit, self.count, self.reservations, self.decrements = limit, 0, {}, 0
        self.lock = RLock()

    def reserve(self, reservation_id: str) -> bool:
        with self.lock:
            if self.count >= self.limit:
                return False
            self.count += 1
            self.reservations[reservation_id] = Reservation()
            return True

    def transition(self, reservation_id: str, target: str) -> None:
        with self.lock:
            reservation = self.reservations[reservation_id]
            if reservation.state != "reserved":
                return
            reservation.state = target
            if target == "refunded":
                self.count -= 1
                self.decrements += 1


def test_first_request_creates_exactly_one_quota_unit():
    store = FakeTransactions(3)
    assert store.reserve("r1")
    assert store.count == 1
    assert len(store.reservations) == 1


def test_sequential_quota_boundary_is_exact():
    store = FakeTransactions(3)
    assert [store.reserve(f"r{i}") for i in range(4)] == [True, True, True, False]
    assert store.count == 3
    assert len(store.reservations) == 3


def test_twenty_concurrent_reservations_allow_exactly_three():
    store, barrier = FakeTransactions(3), Barrier(20)
    def reserve(index):
        barrier.wait()
        return store.reserve(f"r{index}")
    with ThreadPoolExecutor(max_workers=20) as executor:
        outcomes = list(executor.map(reserve, range(20)))
    assert sum(outcomes) == 3
    assert outcomes.count(False) == 17
    assert store.count == 3
    assert len(store.reservations) == 3


def test_existing_row_race_allows_only_remaining_capacity():
    store, barrier = FakeTransactions(3), Barrier(10)
    assert store.reserve("existing")
    def reserve(index):
        barrier.wait()
        return store.reserve(f"r{index}")
    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes = list(executor.map(reserve, range(10)))
    assert sum(outcomes) == 2
    assert outcomes.count(False) == 8
    assert store.count == 3


def test_concurrent_double_refund_decrements_once():
    store, barrier = FakeTransactions(3), Barrier(2)
    assert store.reserve("r1")
    def refund():
        barrier.wait()
        store.transition("r1", "refunded")
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: refund(), range(2)))
    assert store.decrements == 1
    assert store.count == 0
    assert store.reservations["r1"].state == "refunded"


def test_finalize_refund_race_keeps_terminal_state_consistent():
    store, barrier = FakeTransactions(3), Barrier(2)
    assert store.reserve("r1")
    def transition(target):
        barrier.wait()
        store.transition("r1", target)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(transition, ("consumed", "refunded")))
    state = store.reservations["r1"].state
    assert (state, store.count) in {("consumed", 1), ("refunded", 0)}
