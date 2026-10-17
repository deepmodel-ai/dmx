---
name: sync
title: Sync Shared Sources (now /dmx/update-shared-sources)
description: Renamed to /dmx/update-shared-sources in 0.6, which locks shared sources in .dmx/shared-sources.lock instead of copying them into .dmx/vendor/. Kept as an alias for 0.6.
---

`/dmx/sync` is now `/dmx/update-shared-sources`. Tell the developer that in one line: "`/dmx/sync` is now `/dmx/update-shared-sources`; this alias goes away in a later release."

Then call `get_skill_definition` with name `update-shared-sources` and follow its steps exactly, with no arguments: only sources that are new or whose address changed are looked up again.
