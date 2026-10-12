# sticky-panote

The notification-area menu provides one AutoMove control that switches between
Start and Stop according to the current worker state.

While Sticky Panote is running (including hidden in the tray), Hubstaff tracking
automatically pauses daily at 12:00 PM and resumes at 1:00 PM, Asia/Manila time.
The schedule checks every five seconds and saves a pending 1:00 PM resume during
lunch, including when Hubstaff is already stopped. Restarting Sticky Panote
during the break still allows it to resume afterward. Starting Hubstaff during
lunch is disabled.
