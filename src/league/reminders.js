function getMoscowClock(now = new Date()) {
  try {
    const formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Moscow",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

    const parts = formatter.formatToParts(now);
    const map = new Map(parts.map((part) => [part.type, part.value]));

    return {
      year: Number(map.get("year")),
      month: Number(map.get("month")),
      day: Number(map.get("day")),
      hour: Number(map.get("hour")),
      minute: Number(map.get("minute")),
    };
  } catch (_) {
    const utcMillis = now.getTime() + now.getTimezoneOffset() * 60 * 1000;
    const msKDate = new Date(utcMillis + 3 * 60 * 60 * 1000);
    return {
      year: msKDate.getUTCFullYear(),
      month: msKDate.getUTCMonth() + 1,
      day: msKDate.getUTCDate(),
      hour: msKDate.getUTCHours(),
      minute: msKDate.getUTCMinutes(),
    };
  }
}

function dueSlots(now = new Date()) {
  const time = getMoscowClock(now);

  if (time.minute !== 0) {
    return [];
  }

  const dateCode = `${time.year}${String(time.month).padStart(2, "0")}${String(
    time.day
  ).padStart(2, "0")}`;
  const hourCode = String(time.hour).padStart(2, "0");
  const slots = [{ type: "hourly", key: `hourly:${dateCode}:${hourCode}` }];

  if ([9, 15, 20].includes(time.hour)) {
    slots.push({ type: "daily", key: `daily:${dateCode}:${hourCode}` });
  }

  return slots;
}

module.exports = {
  dueSlots,
};
