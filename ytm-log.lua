-- ytm-log.lua: the listening log, written from inside mpv (ytm v0.1, DESIGN.md §1).
-- one row per play in ~/.local/share/ytm/plays/YYYY-MM.jsonl. the cli hands over *why* a track was queued through
-- user-data/ytm/src/<vid>; this script measures what was actually heard. nothing here leaves the mac.
-- 0.8.2: it also keeps the music going: a retry resumes where the song was, a dead network gets ten quiet minutes of
-- one-every-30-s tries, an ended queue refills itself once, and three dead songs ask for a yt-dlp upgrade (once per 6 h).
-- the pure rules live in M (returned at the end): tests/test_log.py runs them, and this whole file, under a fake mp in luajit.
local utils = require "mp.utils"
local msg = require "mp.msg"

local DATA = os.getenv("YTM_DATA") or (os.getenv("HOME") .. "/.local/share/ytm")  -- tests use a scratch dir
local YTM = os.getenv("YTM_BIN") or "ytm"      -- the cli exports YTM_BIN to mpv; tests swap in a recorder
local PLAYS = DATA .. "/plays"
local OPEN = PLAYS .. "/.open.json"
if not utils.file_info(PLAYS) then os.execute("mkdir -p '" .. PLAYS .. "'") end  -- a shell only the first time, not every start
math.randomseed(os.time())

local M = {}
M.LONG = 20 * 60          -- past this it's a mix: 3 minutes of an hour isn't a verdict on the song
M.WAIT_EVERY, M.WAIT_FOR = 30, 600
M.UPDATE_GAP = 6 * 3600

-- finished / partial / skipped / stopped. a length that arrived late (hls says 0 at file-loaded) or a mix never makes a skip:
-- long mixes heard for minutes were logged as skips and taught the ranking the wrong thing
function M.outcome(reason, heard, dur, late)
  if reason == "eof" then return "finished" end
  if reason == "error" then return "partial" end     -- the network cut it, not you: never teach v1 a false skip
  if dur and dur > 0 and heard >= 0.9 * dur then return "finished" end
  if reason == "stopped" then return "stopped" end   -- our own marker for stop/quit/crash recovery
  if not dur or dur <= 0 then return "partial" end   -- live streams are never "skipped"
  if late or dur > M.LONG then return "partial" end
  if heard < 0.5 * dur and heard < 240 then return "skipped" end
  return "partial"
end

-- the queue really ran out: a natural end of its last entry (not stop, quit, an error or a replace)
function M.should_refill(reason, pos, count)
  return reason == "eof" and pos ~= nil and count ~= nil and count > 0 and pos >= count - 1
end

-- where a retry starts: where the song was, unless that's the first seconds anyway
function M.resume_at(t) if t and t > 5 then return math.floor(t) end return nil end

-- at most once per gap; nil means never ran
function M.due(now, last, gap) return last == nil or now - last >= gap end


-- 0.9.2, ready before you ask (snappier loading): yt-dlp needs ~4-5 s to resolve one track, so a play
-- never waits for it if the song is already on disk (lib/offline.py's cache) or was resolved ahead (DATA/urls.json, written by
-- the watcher and by the search cards). pure parts here, the hook below.
local EXTS = {"opus", "webm", "m4a", "mp4", "mp3"}
function M.local_file(dir, vid, info)
  for _, e in ipairs(EXTS) do
    local p = dir .. "/audio/watch?v=" .. vid .. "." .. e
    local fi = info(p)
    if fi and fi.is_file and fi.size > 50000 then return p end
  end
end
-- a resolved url is good while it has 15 more minutes (googlevideo urls carry their own expiry)
function M.fresh_url(urls, vid, now)
  local r = type(urls) == "table" and urls[vid] or nil
  if r and type(r.url) == "string" and type(r.exp) == "number" and r.exp - now > 900 then return r.url end
end
function M.youtube_watch(path) return path ~= nil and path:match("^https?://[%w%.%-]*youtube%.com/") ~= nil end

local cur, acc, last_t = nil, 0, nil

local function now_iso()
  local z = os.date("%z")                       -- +0530 → +05:30
  return os.date("%Y-%m-%dT%H:%M:%S") .. z:sub(1, 3) .. ":" .. z:sub(4, 5)
end

local function append(path, line)
  local f = io.open(path, "a"); if not f then msg.warn("ytm-log: can't write " .. path); return end
  f:write(line); f:close()
end

local function month_file() return PLAYS .. "/" .. os.date("%Y-%m") .. ".jsonl" end

local function vid_of(path)
  return path and path:match("[?&]v=([%w_-]+)") or nil
end

local function close(reason)
  if not cur then return end
  cur.listened = math.floor(acc * 10 + 0.5) / 10
  cur["end"] = now_iso()
  cur.outcome = M.outcome(reason, acc, cur.dur, cur.dur_late)
  cur.vol = mp.get_property_number("volume")
  append(month_file(), utils.format_json(cur) .. "\n")
  os.remove(OPEN)
  cur, acc, last_t = nil, 0, nil
end

-- the cli, detached: mpv never waits on it, and it never holds mpv's pipes
local function ytm(...)
  local args = {YTM, ...}
  msg.info("ytm-log: running ytm " .. table.concat(args, " ", 2))
  mp.command_native({name = "subprocess", args = args, playback_only = false, detach = true})
end

-- crash recovery: a play left open by a killed mpv is closed as "stopped" on the next start
do
  local f = io.open(OPEN, "r")
  if f then
    local row = utils.parse_json(f:read("*a") or ""); f:close()
    if row then row.outcome = "stopped"; row.recovered = true; append(month_file(), utils.format_json(row) .. "\n") end
    os.remove(OPEN)
  end
end

local refill_armed = false   -- one refill per ending: armed by a song that opened, spent by the refill
local recovering, wait_timer = nil, nil
local last_update = nil

mp.register_event("file-loaded", function()
  close("redirect")                              -- defensive: a previous play that never got its end-file
  local path = mp.get_property("path")
  local vid = vid_of(path)
  local raw = vid and mp.get_property_native("user-data/ytm-src-" .. vid) or nil   -- flat keys: nested user-data paths don't auto-create
  local intent = type(raw) == "table" and raw or (type(raw) == "string" and utils.parse_json(raw)) or nil
  intent = intent or {}
  local mode = mp.get_property_native("user-data/ytm-mode") or "normal"
  local t = os.date("*t")
  cur = {v = 1, id = string.format("%x%x", os.time(), math.random(0, 0xffffff)), start = now_iso(),
         vid = vid, title = intent.title or mp.get_property("media-title"), artist = intent.artist, album = intent.album,
         dur = mp.get_property_number("duration") or 0, source = intent.source or "unknown", query = intent.query,
         seed_vid = intent.seed_vid, app = "ytm", hour = t.hour, dow = t.wday - 1, mode = mode}
  acc, last_t = 0, mp.get_property_number("playback-time")
  refill_armed = true
end)

-- seconds heard: playback-time advances while unpaused; a jump over 2 s is a seek and doesn't count
mp.observe_property("playback-time", "number", function(_, t)
  if not cur or not t then return end
  if cur.dur == 0 then
    cur.dur = mp.get_property_number("duration") or 0
    if cur.dur > 0 then cur.dur_late = true end   -- the length came after the start: kept in the row, never a skip
  end
  if last_t and not mp.get_property_bool("pause") then
    local d = t - last_t
    if d > 0 and d < 2 then acc = acc + d end
  end
  last_t = t
end)


-- the hook runs before mpv's own yt-dlp hook (priority 10), and swaps what gets opened, never what `path` says: the log, the
-- queue and the lights still see the watch url, so a cached play is logged like any other
local URLS = DATA .. "/urls.json"
local function read_urls()
  local f = io.open(URLS, "r"); if not f then return nil end
  local txt = f:read("*a"); f:close()
  return utils.parse_json(txt)
end
local used = {}   -- vid -> {kind = "file" | "url", src = ...}: what the last open of it used, so a failure can drop the culprit
mp.add_hook("on_load", 5, function()
  local path = mp.get_property("stream-open-filename")
  if not M.youtube_watch(path) then return end
  local vid = vid_of(path); if not vid then return end
  local f = M.local_file(DATA, vid, utils.file_info)
  if f then used[vid] = {kind = "file", src = f}; mp.set_property("stream-open-filename", f); return end
  local u = M.fresh_url(read_urls(), vid, os.time())
  if u then used[vid] = {kind = "url", src = u}; mp.set_property("stream-open-filename", u) end
end)
-- a swapped source that then fails (a corrupt file, a url bound to another network) must not be tried again by the retry
local function drop_source(path)
  local vid = vid_of(path); local u = vid and used[vid]; if not u then return end
  used[vid] = nil
  if u.kind == "file" then os.remove(u.src); return end
  local urls = read_urls()
  if type(urls) == "table" and urls[vid] then
    urls[vid] = nil
    local tmp = URLS .. ".tmp"
    local f = io.open(tmp, "w"); if f then f:write(utils.format_json(urls)); f:close(); os.rename(tmp, URLS) end
  end
end

-- a retry (or a recovery try) opens at the second it broke, not 0:00: set as a file-local start just before the open
local resume = nil
mp.add_hook("on_load", 50, function()
  local r = resume; resume = nil
  if r and r.t and mp.get_property("path") == r.path then mp.set_property("file-local-options/start", tostring(r.t)) end
end)

-- a track that fails to open (youtube sometimes refuses the first hls segment) gets one more try instead of a silent skip.
-- 10-23: the retry replays the same playlist entry (the old insert-at-play left a duplicate per failure), each path is
-- tried twice at most, and three tracks in a row that fail twice stop the march: a dead network used to walk the whole
-- radio, one yt-dlp per attempt (~50 spawns in a burst).
local started, started_pos, retried, nretried, given_up = nil, nil, {}, 0, 0
mp.register_event("start-file", function()
  started, started_pos = mp.get_property("path"), mp.get_property_number("playlist-playing-pos")
end)

local function at_pos(pos, path)   -- that entry is still where it was: a "ytm <song>" (replace) in between must win
  return pos and pos >= 0 and mp.get_property("playlist/" .. pos .. "/filename") == path
end

local function retry(path, pos, t)
  if nretried > 200 then retried, nretried = {}, 0 end   -- a long session doesn't keep every path forever
  retried[path] = true; nretried = nretried + 1
  if at_pos(pos, path) then
    resume = {path = path, t = M.resume_at(t)}
    mp.commandv("playlist-play-index", tostring(pos))
  end
end

local function end_wait(why)
  if wait_timer then wait_timer:kill() end
  if recovering and why then msg.warn("ytm-log: " .. why) end
  recovering = nil
end

-- after the stop: one timer, a try every 30 s for 10 min (20 opens at most, mpv's own yt-dlp each), and the first song that
-- opens ends it (file-loaded). the queue changing under it, or a song playing again, ends it too
local function wait_tick()
  local r = recovering
  if not r then return end_wait() end
  if os.time() >= r.deadline then return end_wait("ten minutes and the network's still out; ytm n when it's back") end
  if not at_pos(r.pos, r.path) then return end_wait() end
  if not mp.get_property_bool("idle-active") then return end   -- something is opening or playing: let it be
  r.tries = r.tries + 1
  resume = {path = r.path, t = M.resume_at(r.t)}
  mp.commandv("playlist-play-index", tostring(r.pos))
end

local function stop_and_wait(path, pos, t)
  msg.warn("ytm-log: three songs in a row wouldn't open; stopping here, trying again every 30 s for 10 min (ytm n to try now)")
  mp.commandv("stop", "keep-playlist")
  given_up = 0   -- the next ytm n / ytm <song> gets a fresh three: before, a failure after the stop counted 4, 5, 6… and never stopped again
  if M.due(os.time(), last_update, M.UPDATE_GAP) then last_update = os.time(); ytm("update", "--broken") end   -- maybe yt-dlp, not the wifi
  recovering = {path = path, pos = pos, t = t, deadline = os.time() + M.WAIT_FOR, tries = 0}
  if wait_timer then wait_timer:kill(); wait_timer:resume() else wait_timer = mp.add_periodic_timer(M.WAIT_EVERY, wait_tick) end
end

mp.register_event("end-file", function(e)
  local path, pos, t = started, started_pos, last_t   -- last_t: where the song was (close() below clears it)
  started, started_pos = nil, nil
  if e.reason == "error" and path then
    pcall(drop_source, path)
    if recovering then
      mp.commandv("stop", "keep-playlist")   -- still out: no march down the queue, the timer tries again
    elseif not retried[path] and given_up < 3 then
      retry(path, pos, t)
    else
      given_up = given_up + 1
      if given_up >= 3 then stop_and_wait(path, pos, t) end
    end
  elseif M.should_refill(e.reason, pos, mp.get_property_number("playlist-count")) and refill_armed then
    local vid = vid_of(path)
    refill_armed = false
    -- a second later, and only if nothing else got queued meanwhile: the cli locks it too and checks idle again
    if vid then mp.add_timeout(1, function() if mp.get_property_bool("idle-active") then ytm("radio", "--refill", vid) end end) end
  end
  close(e.reason == "eof" and "eof" or (e.reason == "quit" and "stopped" or e.reason))
end)
mp.register_event("file-loaded", function()   -- one song that opens resets the breaker and ends a wait
  given_up = 0
  if recovering then msg.info("ytm-log: back after " .. recovering.tries .. " tries"); end_wait() end
end)

mp.register_event("shutdown", function() close("stopped") end)

-- the cli leaves one user-data/ytm-src-<vid> per queued song; a long day of radios would keep them all. every 5 min, a key
-- whose song has left the playlist goes, but only on its second sighting (the cli sets the key just before the loadfile)
local stale, ticks = {}, 0
local function sweep()
  local ud = mp.get_property_native("user-data")
  if type(ud) ~= "table" then return end
  local live = {}
  for _, e in ipairs(mp.get_property_native("playlist") or {}) do local v = vid_of(e.filename); if v then live[v] = true end end
  local seen = {}
  for k in pairs(ud) do
    local v = type(k) == "string" and k:match("^ytm%-src%-(.+)$")
    if v and not live[v] then
      if stale[v] then mp.del_property("user-data/" .. k) else seen[v] = true end
    end
  end
  stale = seen
end

-- every 30 s the open play goes to .open.json, so a kill -9 loses at most 30 s of one row
mp.add_periodic_timer(30, function()
  ticks = ticks + 1
  if ticks % 10 == 0 then pcall(sweep) end
  if not cur then return end
  local snap = {}
  for k, v in pairs(cur) do snap[k] = v end
  snap.listened = math.floor(acc * 10 + 0.5) / 10
  snap["end"] = now_iso()
  local tmp = OPEN .. ".tmp"                     -- write then rename: a kill mid-write never leaves half a row to recover
  local f = io.open(tmp, "w"); if f then f:write(utils.format_json(snap)); f:close(); os.rename(tmp, OPEN) end
end)

return M
