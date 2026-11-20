-- **************************************************
-- AutoRig Studio - Moho API probe (READ-ONLY discovery script)
-- GENERATED FILE - edit src/autorig/data/templates/probe_moho.lua and
-- regenerate with `python -m autorig build-probe` instead of editing this copy.
--
-- Purpose: find out, from the running Moho (14.5+), facts that are not
-- documented anywhere we can reach from the cloud - in particular whether
-- any Lua API exists for Live Mode inputs (webcam / gamepad / microphone).
--
-- It changes NOTHING in the document. It:
--   * lists the globals, the MOHO / LM / LM.GUI tables (incl. constant values)
--   * lists the members visible through the metatables of real Moho objects
--     (ScriptInterface, MohoDoc, layers, bone layer, skeleton, bone, channel)
--   * lists every layer's actions and whether each is a Smart Bone action
--   * flags names that look related to live input
-- and writes everything to a JSON report.
--
-- Run it on the document that holds the AutoRig Phase 0 rig (so bone objects
-- exist), ideally after assigning one camera input and one gamepad input to
-- a Smart Bone / action by hand (see docs/live-models.md, discovery protocol).
-- **************************************************

ScriptName = "AutoRig_Probe"

AutoRig_Probe = {}

-- @@AUTORIG_DATA@@

local SELF = AutoRig_Probe
local MAX_DEPTH = 6
local KEYWORDS = {
	"live", "input", "gamepad", "joystick", "controller", "xinput", "webcam", "camera",
	"track", "face", "facial", "mouth", "blink", "eye", "head", "thumb", "stick", "button",
	"trigger", "micro", "volume", "smart", "vtuber", "stream", "virtual", "physics", "play",
}

function AutoRig_Probe:Name()
	return "AutoRig Probe: Moho API discovery"
end

function AutoRig_Probe:Version()
	return "0.1.0"
end

function AutoRig_Probe:Description()
	return "Read-only: records the Moho Lua API surface and the document's actions to a JSON report for AutoRig Studio."
end

function AutoRig_Probe:Creator()
	return "AutoRig Studio"
end

function AutoRig_Probe:UILabel()
	return "AutoRig Probe: Moho API discovery (read-only)"
end

function AutoRig_Probe:IsEnabled(moho)
	return true
end

-- **************************************************
-- JSON (same encoder as the Phase 0 builder)
-- **************************************************

local function is_integer(n)
	if math.type then
		return math.type(n) == "integer"
	end
	return n == math.floor(n)
end

local function json_escape(s)
	s = s:gsub('[%c"\\]', function(c)
		if c == '"' then return '\\"' end
		if c == "\\" then return "\\\\" end
		if c == "\n" then return "\\n" end
		if c == "\r" then return "\\r" end
		if c == "\t" then return "\\t" end
		return string.format("\\u%04x", string.byte(c))
	end)
	return s
end

-- Arrays are tables marked with SELF.array(); everything else is an object.
local ARRAY_MT = {}
function SELF.array(t)
	return setmetatable(t or {}, ARRAY_MT)
end

local function json_encode(v, indent)
	indent = indent or ""
	local t = type(v)
	if v == nil then
		return "null"
	elseif t == "boolean" then
		return v and "true" or "false"
	elseif t == "number" then
		if v ~= v or v == math.huge or v == -math.huge then
			return "null"
		end
		if is_integer(v) then
			return string.format("%d", v)
		end
		return string.format("%.12g", v)
	elseif t == "string" then
		return '"' .. json_escape(v) .. '"'
	elseif t == "table" then
		local inner = indent .. "  "
		local parts = {}
		if getmetatable(v) == ARRAY_MT then
			for i = 1, #v do
				parts[#parts + 1] = inner .. json_encode(v[i], inner)
			end
			if #parts == 0 then return "[]" end
			return "[\n" .. table.concat(parts, ",\n") .. "\n" .. indent .. "]"
		end
		local keys = {}
		for k in pairs(v) do
			keys[#keys + 1] = tostring(k)
		end
		table.sort(keys)
		for _, k in ipairs(keys) do
			parts[#parts + 1] = inner .. '"' .. json_escape(k) .. '": ' .. json_encode(v[k], inner)
		end
		if #parts == 0 then return "{}" end
		return "{\n" .. table.concat(parts, ",\n") .. "\n" .. indent .. "}"
	end
	return '"<' .. t .. '>"'
end
SELF.json_encode = json_encode

-- **************************************************
-- Introspection helpers (pure Lua; every step is pcall-guarded)
-- **************************************************

local function sorted_members(map)
	local names = {}
	for name in pairs(map) do names[#names + 1] = name end
	table.sort(names)
	local out = SELF.array()
	for _, name in ipairs(names) do
		out[#out + 1] = { name = name, type = map[name] }
	end
	return out
end

-- Members of a plain table (e.g. MOHO, LM), with scalar values for constants.
local function table_members(t)
	local out = SELF.array()
	if type(t) ~= "table" then return out end
	local ok, err = pcall(function()
		local names = {}
		for k in pairs(t) do
			if type(k) == "string" then names[#names + 1] = k end
		end
		table.sort(names)
		for _, k in ipairs(names) do
			local v = rawget(t, k)
			local entry = { name = k, type = type(v) }
			if type(v) == "number" or type(v) == "boolean" or type(v) == "string" then
				entry.value = v
			end
			out[#out + 1] = entry
		end
	end)
	if not ok then
		out[#out + 1] = { name = "<error>", type = tostring(err) }
	end
	return out
end

-- Members reachable through an object's metatable chain (no Moho calls).
local function surface(obj)
	local found, seen, notes = {}, {}, SELF.array()
	local function add_keys(t, depth)
		if type(t) ~= "table" or seen[t] or depth > MAX_DEPTH then return end
		seen[t] = true
		for k, v in pairs(t) do
			if type(k) == "string" and found[k] == nil then
				found[k] = type(v)
			end
		end
		for _, special in ipairs({ ".get", ".set", "__index", "__parent", "__base", ".base" }) do
			local sub = rawget(t, special)
			if type(sub) == "table" then add_keys(sub, depth + 1) end
		end
		local mt = getmetatable(t)
		if type(mt) == "table" then add_keys(mt, depth + 1) end
	end
	local ok, err = pcall(function()
		local mt = getmetatable(obj)
		if type(mt) == "table" then
			add_keys(mt, 0)
			if type(mt.__index) ~= "table" then
				notes[#notes + 1] = "__index is a " .. type(mt.__index) .. " (methods may not be enumerable)"
			end
		elseif mt == nil then
			notes[#notes + 1] = "no metatable"
		else
			notes[#notes + 1] = "metatable is protected (" .. type(mt) .. ")"
		end
	end)
	if not ok then notes[#notes + 1] = "introspection error: " .. tostring(err) end
	return { members = sorted_members(found), notes = notes, lua_type = type(obj) }
end
SELF.surface = surface

local function keyword_hits(where, members, hits)
	for _, m in ipairs(members) do
		local lname = string.lower(tostring(m.name))
		for _, kw in ipairs(KEYWORDS) do
			if string.find(lname, kw, 1, true) then
				hits[#hits + 1] = { where = where, name = m.name, keyword = kw }
				break
			end
		end
	end
end
SELF.keyword_hits = keyword_hits

-- **************************************************
-- Document walk (read-only, allowlisted Moho calls only)
-- **************************************************

local function layer_actions(layer)
	local out = SELF.array()
	for i = 0, layer:CountActions() - 1 do
		local name = layer:ActionName(i)
		out[#out + 1] = { name = name, smart_bone = layer:IsSmartBoneAction(name) }
	end
	return out
end

local function walk_layer(moho, layer, path, depth, ctx)
	local entry = { name = layer:Name(), path = path, type = layer:LayerType() }
	local okA, acts = pcall(layer_actions, layer)
	if okA then entry.actions = acts else entry.actions_error = tostring(acts) end

	if entry.type == MOHO.LT_BONE and ctx.samples.MohoBoneLayer == nil then
		local bl = moho:LayerAsBone(layer)
		ctx.samples.MohoBoneLayer = bl
		local skel = bl:Skeleton()
		ctx.samples.M_Skeleton = skel
		if skel:CountBones() > 0 then
			local bone = skel:Bone(0)
			ctx.samples.M_Bone = bone
			ctx.samples.AnimChannel = bone.fAnimAngle
			ctx.samples.LM_Vector2 = bone.fPos
		end
	end
	if entry.type == MOHO.LT_IMAGE and ctx.samples.MohoImageLayer == nil then
		ctx.samples.MohoImageLayer = moho:LayerAsImage(layer)
	end
	if ctx.samples.MohoLayer == nil then
		ctx.samples.MohoLayer = layer
	end

	if layer:IsGroupType() and depth < MAX_DEPTH then
		local group = moho:LayerAsGroup(layer)
		if ctx.samples.MohoGroupLayer == nil then ctx.samples.MohoGroupLayer = group end
		entry.children = SELF.array()
		for i = 0, group:CountLayers() - 1 do
			entry.children[#entry.children + 1] =
				walk_layer(moho, group:Layer(i), path .. "/" .. i, depth + 1, ctx)
		end
	end
	return entry
end

-- **************************************************
-- Entry point
-- **************************************************

function AutoRig_Probe:Run(moho)
	local D = SELF.DATA
	local report = {
		autorig_probe_version = "0.1.0",
		generator = D.generator,
		lua_version = _VERSION,
		errors = SELF.array(),
		warnings = SELF.array(),
		keyword_hits = SELF.array(),
	}
	local okV, appVersion = pcall(function() return moho:AppVersion() end)
	report.moho_app_version = okV and appVersion or ("unavailable: " .. tostring(appVersion))

	-- globals and well-known tables
	local globals = {}
	for k, v in pairs(_G) do
		if type(k) == "string" then globals[k] = type(v) end
	end
	report.globals = sorted_members(globals)
	report.tables = {
		MOHO = table_members(MOHO),
		LM = table_members(LM),
	}
	local okGui, gui = pcall(function() return LM.GUI end)
	report.tables["LM.GUI"] = okGui and table_members(gui) or SELF.array()

	-- document structure + object samples
	local ctx = { samples = { ScriptInterface = moho } }
	local okDoc, errDoc = pcall(function()
		local doc = moho.document
		ctx.samples.MohoDoc = doc
		report.document = { layers = SELF.array() }
		for i = 0, doc:CountLayers() - 1 do
			report.document.layers[#report.document.layers + 1] = walk_layer(moho, doc:Layer(i), tostring(i), 0, ctx)
		end
	end)
	if not okDoc then
		report.errors[#report.errors + 1] = "Document walk failed: " .. tostring(errDoc)
	end

	report.surfaces = {}
	for kind, obj in pairs(ctx.samples) do
		report.surfaces[kind] = surface(obj)
	end
	for _, kind in ipairs({ "MohoBoneLayer", "M_Skeleton", "M_Bone", "AnimChannel", "MohoImageLayer" }) do
		if ctx.samples[kind] == nil then
			report.warnings[#report.warnings + 1] = "No " .. kind .. " found to inspect (run the probe on the Phase 0 rig document)."
		end
	end

	for kind, s in pairs(report.surfaces) do keyword_hits("surface:" .. kind, s.members, report.keyword_hits) end
	for name, members in pairs(report.tables) do keyword_hits("table:" .. name, members, report.keyword_hits) end
	keyword_hits("globals", report.globals, report.keyword_hits)

	local text = json_encode(report) .. "\n"
	local f, err = io.open(D.report_path, "w")
	local where
	if f then
		f:write(text)
		f:close()
		where = "Report: " .. D.report_path
	else
		where = "Could not write " .. tostring(D.report_path) .. ": " .. tostring(err)
	end
	print("[AutoRig Probe] " .. where)
	LM.GUI.Alert(LM.GUI.ALERT_INFO,
		"AutoRig Probe finished (nothing was changed).",
		string.format("%d keyword hits, %d errors, %d warnings.", #report.keyword_hits, #report.errors, #report.warnings),
		where,
		MOHO.Localize("/Scripts/OK=OK"), nil, nil)
end
