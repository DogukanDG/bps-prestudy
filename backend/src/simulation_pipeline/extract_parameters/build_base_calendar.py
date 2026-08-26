from typing import List, Dict
import pandas as pd


def build_base_calendar() -> pd.DataFrame:
    """
    Construct a weekly base calendar with 1-hour slots from 00:00 to 24:00.

    The calendar covers Monday through Sunday. For each day, 24 slots are
    created:
      - slot_id is a running integer starting at 1.
      - beginTime goes from "HH:00:00" for HH = 00..23.
      - endTime is "HH+1:00:00" except for the last slot (23:00–24:00),
        which uses "23:59:59.999000" as the end time.

    Returns
    -------
    pd.DataFrame
        DataFrame with one row per slot and columns:
        ['slot_id', 'day', 'beginTime', 'endTime'].
    """
    day_order: List[str] = [
        "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY",
        "FRIDAY", "SATURDAY", "SUNDAY"
    ]

    base_calendar: List[Dict] = []
    slot_id = 1

    for day in day_order:
        for hour in range(0, 24):  # 00:00 to 23:00
            begin_time = f"{hour:02}:00:00"

            if hour == 23:
                end_time = "23:59:59.999000"
            else:
                end_time = f"{hour + 1:02}:00:00"

            base_calendar.append({
                "slot_id": slot_id,
                "day": day,
                "beginTime": begin_time,
                "endTime": end_time,
            })
            slot_id += 1

    df = pd.DataFrame(base_calendar)
    return df


def add_neighboring_slots(df_base_calendar: pd.DataFrame) -> pd.DataFrame:
    """
    Annotate a base calendar with neighboring slot IDs within the same day.

    For each row (slot) in the input calendar, this function computes the
    previous and next slot in the same day and stores them as a list of
    integers in a new column 'neighboring_slots'. The first slot of the
    day has only a next neighbor; the last slot has only a previous one.

    Parameters
    ----------
    df_base_calendar : pd.DataFrame
        Base calendar DataFrame with at least:
        ['slot_id', 'day', 'beginTime', 'endTime'].

    Returns
    -------
    pd.DataFrame
        A copy of the input DataFrame, sorted by ['day', 'slot_id'], with
        an additional column:
          - 'neighboring_slots' : list[int] of neighboring slot_ids in
            the same day.
    """
    # Ensure consistent ordering within each day
    df = df_base_calendar.sort_values(["day", "slot_id"]).reset_index(drop=True)

    # Compute prev/next slot_id within the same day
    g = df.groupby("day", sort=False)
    df["prev_slot"] = g["slot_id"].shift(1)
    df["next_slot"] = g["slot_id"].shift(-1)

    # Build list of neighbors (drop NaNs, cast to int)
    df["neighboring_slots"] = df[["prev_slot", "next_slot"]].apply(
        lambda s: [int(x) for x in s if pd.notna(x)],
        axis=1,
    )

    # Drop helper cols
    df = df.drop(columns=["prev_slot", "next_slot"])

    return df


def build_calendar_with_neighbors() -> pd.DataFrame:
    """
    Build a full weekly base calendar and annotate neighboring slots.

    Internally:
      1. Calls `build_base_calendar` to create a 7-day calendar
         (Monday–Sunday) with 1-hour slots from 00:00 to 24:00.
      2. Calls `add_neighboring_slots` to add a 'neighboring_slots'
         list column.
      3. Sorts the final result by 'slot_id'.

    Returns
    -------
    pd.DataFrame
        Calendar DataFrame with columns:
        ['slot_id', 'day', 'beginTime', 'endTime', 'neighboring_slots'].
    """
    df_base = build_base_calendar()
    df_full = add_neighboring_slots(df_base)
    df_full = df_full.sort_values("slot_id").reset_index(drop=True)
    return df_full
