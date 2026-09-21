-- calendar_events was created by 001 for Step 5 (Google Calendar sync), which
-- isn't built yet. Nothing populates it, and the two readers that queried it
-- (Dashboard's priority feed, the goal roadmap's calendar_context) already
-- treat "no deadlines" as a fully supported state -- so there is nothing this
-- table is currently backing. Dropped rather than left idle; it comes back
-- with Step 5 once there is a writer for it.
DROP TABLE calendar_events;
