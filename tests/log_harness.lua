-- the whole of ytm-log.lua under a fake mp, in luajit: events fired by hand, the clock moved by hand, every command and
-- spawn recorded. no mpv, no network. run by tests/test_log.py (YTM_DATA = a scratch dir, YTM_BIN = a fake path).
local DATA = assert(os.getenv("YTM_DATA"), "YTM_DATA must be a scratch dir")
local SCRIPT = assert(arg[1], "usage: luajit log_harness.lua <ytm-log.lua>")

local fails = 0
local function check(name, ok, detail)
  if not ok then fails = fails + 1 end
  print((ok and "ok   " or "FAIL ") .. name .. (detail and ("  " .. tostring(detail)) or ""))
end

-- ---- the fake mp ----
local NOW = 1000000
os.time = function(t) if t then return 0 end return NOW end   -- the script asks os.time() for deadlines and gaps

local function json(v)
  local t = type(v)
  if t == "string" then return '"' .. v:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"' end
  if t == "number" or t == "boolean" then return tostring(v) end
  if t == "nil" then return "null" end
  local parts = {}
  for k, x in pairs(v) do parts[#parts + 1] = json(tostring(k)) .. ":" .. json(x) end
  table.sort(parts); return "{" .. table.concat(parts, ",") .. "}"
end

local props, events, observers, hooks, timeouts, timers, cmds, spawns, warns = {}, {}, {}, {}, {}, {}, {}, {}, {}
local function fire(name, e) for _, f in ipairs(events[name] or {}) do f(e or {}) end end
local function prop_changed(name) for _, o in ipairs(observers) do if o.name == name then o.fn(name, props[name]) end end end

mp = {
  register_event = function(n, f) events[n] = events[n] or {}; table.insert(events[n], f) end,
  observe_property = function(n, _, f) table.insert(observers, {name = n, fn = f}) end,
  add_hook = function(n, _, f) hooks[n] = hooks[n] or {}; table.insert(hooks[n], f) end,
  add_timeout = function(s, f) local t = {s = s, fn = f}; table.insert(timeouts, t); return t end,
  add_periodic_timer = function(s, f)
    local t = {s = s, fn = f, on = true}
    function t:kill() self.on = false end
    function t:resume() self.on = true end
    table.insert(timers, t); return t
  end,
  get_property = function(n) local v = props[n]; return v ~= nil and tostring(v) or nil end,
  get_property_number = function(n) return tonumber(props[n]) end,
  get_property_bool = function(n) return props[n] == true end,
  get_property_native = function(n) return props[n] end,
  set_property = function(n, v) props[n] = v; table.insert(cmds, {"set", n, v}) end,
  del_property = function(n) props[n] = nil end,
  commandv = function(...) table.insert(cmds, {...}) end,
  command_native = function(c) table.insert(spawns, table.concat(c.args, " ", 2)); return {} end,
}
local FAKE = {files = {}, urls = nil}   -- per test: which paths "exist", what urls.json "contains"
package.preload["mp.utils"] = function()
  return {file_info = function(p) return FAKE.files[p] end, format_json = json, parse_json = function() return FAKE.urls end}   -- nil file_info: the script makes DATA/plays
end
package.preload["mp.msg"] = function()
  return {warn = function(s) table.insert(warns, s) end, info = function() end}
end

local M = dofile(SCRIPT)

local function has_cmd(...)
  local want = table.concat({...}, " ")
  for _, c in ipairs(cmds) do local s = {}; for i, x in ipairs(c) do s[i] = tostring(x) end; if table.concat(s, " ") == want then return true end end
  return false
end
local function run_timeouts() local ts = timeouts; timeouts = {}; for _, t in ipairs(ts) do t.fn() end end
local function url(v) return "https://music.youtube.com/watch?v=" .. v end
local function start(v, pos, count) props.path = url(v); props["playlist-playing-pos"] = pos; props["playlist-count"] = count
  props["playlist/" .. pos .. "/filename"] = url(v); props["idle-active"] = false; fire("start-file") end
local function loaded(dur) props.duration = dur; props["playback-time"] = 0; fire("file-loaded") end
local function play_to(t) local x = props["playback-time"] or 0
  while x < t do x = math.min(t, x + 1); props["playback-time"] = x; prop_changed("playback-time") end end
local function rows()
  local f = io.open(DATA .. "/plays/" .. os.date("%Y-%m") .. ".jsonl"); if not f then return {} end
  local out = {}; for l in f:lines() do out[#out + 1] = l end; f:close(); return out
end

-- 1. the pure rules
check("eof is finished", M.outcome("eof", 10, 200) == "finished")
check("an error is partial, never a skip", M.outcome("error", 3, 200) == "partial")
check("a short listen of a song is a skip", M.outcome("stop", 20, 200) == "skipped")
check("90% heard is finished whatever the reason", M.outcome("stop", 185, 200) == "finished")
check("a late length is partial, not a skip", M.outcome("stop", 20, 200, true) == "partial")
check("13 s of a 2 h mix is partial, not a skip", M.outcome("stop", 13, 7497) == "partial")
check("20 min exactly is still a song", M.outcome("stop", 13, 1200) == "skipped")
check("no length is partial", M.outcome("stop", 13, 0) == "partial")
check("stopped stays stopped", M.outcome("stopped", 13, 200) == "stopped")
check("refill: the last entry ended", M.should_refill("eof", 4, 5) and M.should_refill("eof", 0, 1))
check("refill: not mid-queue", not M.should_refill("eof", 2, 5))
check("refill: not after stop / quit / error", not M.should_refill("stop", 0, 1) and not M.should_refill("quit", 0, 1) and not M.should_refill("error", 0, 1))
check("refill: no position, no refill", not M.should_refill("eof", nil, 1) and not M.should_refill("eof", 0, 0))
check("resume: where it was", M.resume_at(95.7) == 95)
check("resume: the first seconds start over", M.resume_at(3) == nil and M.resume_at(nil) == nil)
check("due: first time, then the gap", M.due(100, nil, 50) and not M.due(120, 100, 50) and M.due(150, 100, 50))

-- 2. a song that ends the queue: one row (finished, the late length marked), one refill a second later, only once
start("vidAAAAAAA1", 0, 1); loaded(0); play_to(3); props.duration = 200; play_to(200)
fire("end-file", {reason = "eof"}); props["idle-active"] = true
check("refill waits a second", #spawns == 0 and #timeouts == 1)
run_timeouts()
check("…then asks the cli for a refill from the last song", spawns[1] == "radio --refill vidAAAAAAA1", spawns[1])
local r = rows()[#rows()] or ""
check("the row is finished, its late length marked", r:find('"outcome":"finished"') and r:find('"dur_late":true') and r:find('"dur":200'), r)
fire("end-file", {reason = "eof"}); run_timeouts()
check("one refill per ending", #spawns == 1, #spawns)
start("vidAAAAAAA2", 0, 1); loaded(180); play_to(20); fire("end-file", {reason = "stop"}); props["idle-active"] = true; run_timeouts()
check("a stop never refills", #spawns == 1, #spawns)
r = rows()[#rows()] or ""
check("20 s of a 3 min song is a skip", r:find('"outcome":"skipped"') and not r:find("dur_late"), r)
start("vidAAAAAAA3", 0, 1); loaded(200); play_to(200); fire("end-file", {reason = "eof"}); props["idle-active"] = false; run_timeouts()
check("something queued in that second: no refill", #spawns == 1, #spawns)
start("vidAAAAAAA4", 0, 1); loaded(3600); play_to(13); fire("end-file", {reason = "stop"})
r = rows()[#rows()] or ""
check("13 s of an hour-long mix is partial", r:find('"outcome":"partial"'), r)

-- 3. a retry resumes where the song was
cmds = {}
start("vidBBBBBBB1", 1, 5); loaded(240); play_to(95); fire("end-file", {reason = "error"})
check("an error replays the same entry", has_cmd("playlist-play-index", "1"), json(cmds))
props["playlist-playing-pos"] = 1; for _, h in ipairs(hooks.on_load) do h() end
check("…from where it was (start=95)", props["file-local-options/start"] == "95", props["file-local-options/start"])
props["file-local-options/start"] = nil; for _, h in ipairs(hooks.on_load) do h() end
check("…once: the next open starts normally", props["file-local-options/start"] == nil)
start("vidBBBBBBB2", 2, 5); fire("end-file", {reason = "error"}); for _, h in ipairs(hooks.on_load) do h() end
check("a song that never opened retries from 0:00", props["file-local-options/start"] == nil and has_cmd("playlist-play-index", "2"))

-- 4. three dead songs: stop, one update, then a try every 30 s for 10 min
cmds = {}; spawns = {}
for i, v in ipairs({"vidCCCCCCC1", "vidCCCCCCC2", "vidCCCCCCC3"}) do
  start(v, i, 5); fire("end-file", {reason = "error"}); start(v, i, 5); fire("end-file", {reason = "error"})
end
check("the third stops the march", has_cmd("stop", "keep-playlist"), json(cmds))
check("…and asks for one update", #spawns == 1 and spawns[1] == "update --broken", json(spawns))
local wait = timers[#timers]
check("…one 30 s timer, not a process", wait and wait.s == 30 and wait.on)
props["idle-active"] = true; cmds = {}
wait.fn()
check("a tick replays the stopped song", has_cmd("playlist-play-index", "3"), json(cmds))
start("vidCCCCCCC3", 3, 5); cmds = {}; fire("end-file", {reason = "error"})
check("still out: stopped again, no retry, no march", has_cmd("stop", "keep-playlist") and not has_cmd("playlist-play-index", "3"), json(cmds))
props["idle-active"] = true; NOW = NOW + 610; cmds = {}; wait.fn()
check("ten minutes on: the wait ends quietly", not wait.on and not has_cmd("playlist-play-index", "3"))
check("…and says so", warns[#warns]:find("ten minutes"), warns[#warns])
wait.fn()
check("an ended wait does nothing", not has_cmd("playlist-play-index", "3"))

-- 5. again inside 6 h: no second update; this time the network comes back
NOW = NOW + 3600
for i, v in ipairs({"vidDDDDDDD1", "vidDDDDDDD2", "vidDDDDDDD3"}) do
  start(v, i, 5); fire("end-file", {reason = "error"}); start(v, i, 5); fire("end-file", {reason = "error"})
end
check("a second stop inside 6 h asks for no update", #spawns == 1, json(spawns))
check("the same timer runs again", timers[#timers] == wait and wait.on, #timers)
props["idle-active"] = true; wait.fn(); start("vidDDDDDDD3", 3, 5); loaded(200)
check("a song that opens ends the wait", not wait.on)
NOW = NOW + 6 * 3600
for i, v in ipairs({"vidEEEEEEE1", "vidEEEEEEE2", "vidEEEEEEE3"}) do
  start(v, i, 5); fire("end-file", {reason = "error"}); start(v, i, 5); fire("end-file", {reason = "error"})
end
check("6 h later the update may run again", #spawns == 2 and spawns[2] == "update --broken", json(spawns))
props["playlist/3/filename"] = url("someoneelse"); props["idle-active"] = true; wait.fn()
check("the queue replaced meanwhile: the wait steps aside", not wait.on)


-- 9. ready before you ask (0.9.2): the on_load swap. the hook opens a cached file or a resolved url instead of asking yt-dlp,
-- and never changes `path`, so the log still sees the watch url
os.execute("mkdir -p '" .. DATA .. "/audio'")
local hook = hooks.on_load[1]
local function swapped(v) props["stream-open-filename"] = url(v); hook(); return props["stream-open-filename"] end
local CF = DATA .. "/audio/watch?v=vidCCCCCCC1.webm"
FAKE.files[CF] = {is_file = true, size = 9000000}
check("a cached song opens from disk", swapped("vidCCCCCCC1") == CF, swapped("vidCCCCCCC1"))
FAKE.files[CF] = {is_file = true, size = 100}
check("a stub file is not a hit", swapped("vidCCCCCCC1") == url("vidCCCCCCC1"))
check("pure: local_file finds the file by extension", M.local_file(DATA, "vidCCCCCCC1", function(p) return p:find("%.m4a$") and {is_file = true, size = 99999} or nil end) == DATA .. "/audio/watch?v=vidCCCCCCC1.m4a")
FAKE.urls = {vidDDDDDDD1 = {url = "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1", exp = NOW + 7200},
             vidEEEEEEE1 = {url = "https://rr1---sn-x.googlevideo.com/old", exp = NOW + 100}}
local f = io.open(DATA .. "/urls.json", "w"); f:write("{}"); f:close()
check("a resolved url opens instead of yt-dlp", swapped("vidDDDDDDD1") == "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1")
check("a url about to expire is not used", swapped("vidEEEEEEE1") == url("vidEEEEEEE1"))
check("nothing cached or resolved: left to yt-dlp", swapped("vidFFFFFFF1") == url("vidFFFFFFF1"))
props["stream-open-filename"] = "https://example.com/a.mp3"; hook()
check("a non-youtube url is left alone", props["stream-open-filename"] == "https://example.com/a.mp3")
check("the hook never rewrites path (the log sees the watch url)", props.path ~= "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1")
-- a swapped source that then fails is dropped, so the retry opens fresh instead of looping on it
local real = DATA .. "/audio/watch?v=vidGGGGGGG1.webm"
local rf = io.open(real, "w"); rf:write(string.rep("x", 10)); rf:close()
FAKE.files[real] = {is_file = true, size = 9000000}
start("vidGGGGGGG1", 0, 1); swapped("vidGGGGGGG1"); fire("end-file", {reason = "error"})
check("a cached file that fails to play is deleted", io.open(real, "r") == nil)
start("vidDDDDDDD1", 0, 1); swapped("vidDDDDDDD1"); fire("end-file", {reason = "error"})
local uf = io.open(DATA .. "/urls.json", "r"); local utxt = uf:read("*a"); uf:close()
check("a resolved url that fails to play is dropped from urls.json", not utxt:find("vidDDDDDDD1", 1, true), utxt)
check("pure: fresh_url wants 15 minutes left", M.fresh_url({a = {url = "u", exp = 2000}}, "a", 1000) == "u" and M.fresh_url({a = {url = "u", exp = 1500}}, "a", 1000) == nil)

print(fails == 0 and "all ok" or (fails .. " FAILED"))
os.exit(fails == 0 and 0 or 1)
