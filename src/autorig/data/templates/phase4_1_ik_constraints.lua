-- **************************************************
-- AutoRig Studio - Phase 4.1 EXPERIMENTAL: IK targets and joint limits on the full-body rig
-- GENERATED FILE - edit src/autorig/data/templates/phase4_1_ik_constraints.lua and
-- regenerate with `python -m autorig native-fullbody` instead of editing this copy.
--
-- Opt-in compatibility layer. It uses M_Bone members that are documented (scripting reference) and present in
-- Moho's own saved files (target_bone, constraints, min_constraint, max_constraint) but have NOT been run
-- natively by AutoRig yet:
--   * links each IK effector (forearms, shins) to its target bone: bone.fTargetBone:SetValue(0, targetId)
--   * sets angle limits: fConstraints = true, fMinConstraint / fMaxConstraint (absolute parent-relative
--     radians = rest + range; whether Moho reads them that way is exactly what this test finds out)
--   * keys the four target bones away from the limbs (frames 120-150) and keys HEAD beyond its limit
--     (frame 160) so renders show what Moho's IK and constraints really do
-- Every write is wrapped in pcall and read back; the report says which operations Moho accepted.
-- Run it on a COPY of the verified rig (the harness saves it as AutoRig_P41_FullBody_IK.moho), because the target
-- constraints change the forearm and shin poses of the FK test frames.
-- **************************************************

ScriptName = "AutoRig_P41_IKConstraints"

AutoRig_P41_IKConstraints = {}

-- @@AUTORIG_DATA@@

local SELF = AutoRig_P41_IKConstraints
local LOG_PREFIX = "[AutoRig P4.1 IK] "

function AutoRig_P41_IKConstraints:Name()
	return "AutoRig Phase 4.1: IK Targets and Joint Limits (experimental)"
end

function AutoRig_P41_IKConstraints:Version()
	return "0.1.0"
end

function AutoRig_P41_IKConstraints:Description()
	return "Experimental: links the AutoRig full-body rig's IK target bones and joint limits, keys IK and limit tests, and writes a report."
end

function AutoRig_P41_IKConstraints:Creator()
	return "AutoRig Studio"
end

function AutoRig_P41_IKConstraints:UILabel()
	return "AutoRig Phase 4.1: IK Targets and Joint Limits (experimental)"
end

function AutoRig_P41_IKConstraints:IsEnabled(moho)
	return true
end

-- **************************************************
-- Small helpers (pure Lua, no Moho API)
-- **************************************************

local function log(msg)
	print(LOG_PREFIX .. msg)
end

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

local function write_text(path, text)
	local f, err = io.open(path, "w")
	if not f then
		return false, tostring(err)
	end
	f:write(text)
	f:close()
	return true
end

local function vec(x, y)
	local v = LM.Vector2:new_local()
	v:Set(x, y)
	return v
end

local function alert(kind, line1, line2, line3)
	LM.GUI.Alert(kind, line1, line2, line3, MOHO.Localize("/Scripts/OK=OK"), nil, nil)
end

-- Names visible through an object's metatable chain. Pure Lua: nothing is called.
local function names_matching(obj, words)
	local found, seen, notes = {}, {}, SELF.array()
	local function add_keys(t, depth)
		if type(t) ~= "table" or seen[t] or depth > 6 then return end
		seen[t] = true
		for k in pairs(t) do
			if type(k) == "string" and string.sub(k, 1, 2) ~= "__" then
				local lname = string.lower(k)
				for _, w in ipairs(words) do
					if string.find(lname, w, 1, true) then
						found[k] = true
						break
					end
				end
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
		else
			notes[#notes + 1] = "metatable not enumerable (" .. type(mt) .. ")"
		end
	end)
	if not ok then notes[#notes + 1] = "introspection error: " .. tostring(err) end
	local names = {}
	for k in pairs(found) do names[#names + 1] = k end
	table.sort(names)
	return { names = SELF.array(names), notes = notes, called = false }
end
SELF.names_matching = names_matching

-- **************************************************
-- Operations (each one pcall-guarded and read back)
-- **************************************************

local function attempt(report, name, fn)
	local ok, res = pcall(fn)
	local result = res
	if not ok then result = tostring(res) end
	report.operations[#report.operations + 1] = { name = name, ok = ok, result = result }
	if not ok then
		report.warnings[#report.warnings + 1] = name .. ": " .. tostring(res)
	end
	return ok, res
end

local function run_ops(moho, D, skel, report)
	for _, L in ipairs(D.links) do
		local eff = skel:Bone(L.effector_id)
		attempt(report, "target:" .. L.effector .. "->" .. L.target, function()
			eff.fTargetBone:SetValue(0, L.target_id)
			return eff.fTargetBone:GetValue(0)
		end)
	end
	for _, C in ipairs(D.constraints) do
		local b = skel:Bone(C.bone_id)
		attempt(report, "constraint:" .. C.bone, function()
			b.fConstraints = true
			b.fMinConstraint = C.min
			b.fMaxConstraint = C.max
			return SELF.array({ b.fConstraints and 1 or 0, b.fMinConstraint, b.fMaxConstraint })
		end)
	end
	for _, M in ipairs(D.target_moves) do
		local id = nil
		for i = 0, skel:CountBones() - 1 do
			if skel:Bone(i):Name() == M.bone then id = i end
		end
		attempt(report, "move:" .. M.bone, function()
			local b = skel:Bone(id)
			local got = SELF.array()
			for _, K in ipairs(M.keys) do
				b.fAnimPos:SetValue(K.frame, vec(K.x, K.y))
				local v = b.fAnimPos:GetValue(K.frame)
				got[#got + 1] = SELF.array({ K.frame, v.x, v.y })
			end
			return got
		end)
	end
	local T = D.clamp_test
	attempt(report, "clamp_test:" .. T.bone, function()
		local b = skel:Bone(T.bone_id)
		b.fAnimAngle:SetValue(T.rest_key_frame, T.rest_value)
		b.fAnimAngle:SetValue(T.frame, T.value)
		return SELF.array({ b.fAnimAngle:GetValue(T.rest_key_frame), b.fAnimAngle:GetValue(T.frame) })
	end)
	attempt(report, "end_frame", function()
		moho.document:SetEndFrame(D.end_frame)
		return moho.document:EndFrame()
	end)
end

function AutoRig_P41_IKConstraints:Run(moho)
	local D = SELF.DATA
	local report = {
		autorig_report_version = "0.1.0",
		kind = "phase4_1_ik_constraints",
		status = "started",
		generator = D.generator,
		lua_version = _VERSION,
		errors = SELF.array(),
		warnings = SELF.array(),
		operations = SELF.array(),
	}
	local okVersion, appVersion = pcall(function() return moho:AppVersion() end)
	report.moho_app_version = okVersion and appVersion or ("unavailable: " .. tostring(appVersion))

	local function finish(kind, title)
		if D.report_path ~= nil and D.report_path ~= "" then
			local ok, err = write_text(D.report_path, json_encode(report) .. "\n")
			if ok then log("Report written: " .. D.report_path) else log("Could not write report: " .. err) end
		end
		alert(kind, title, report.errors[1] or report.warnings[1] or ("Report: " .. tostring(D.report_path)), nil)
	end

	local doc = moho.document
	if doc == nil or D.report_path == nil or D.report_path == "" then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "Open the verified full-body rig first (and use the script generated by the harness)."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 IK: nothing was changed.")
		return
	end
	local layer = doc:LayerByName(D.bone_layer_name)
	if layer == nil or layer:LayerType() ~= MOHO.LT_BONE then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "No bone layer named '" .. D.bone_layer_name .. "'."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 IK: rig not found.")
		return
	end
	local skel = moho:LayerAsBone(layer):Skeleton()
	moho:SetCurFrame(D.rest_frame)
	run_ops(moho, D, skel, report)
	local failed = 0
	for _, op in ipairs(report.operations) do
		if not op.ok then failed = failed + 1 end
	end
	report.failed_operations = failed
	report.status = "completed"
	moho:SetSelLayer(layer)
	doc:SetDirty()
	moho:UpdateUI()
	finish(failed == 0 and LM.GUI.ALERT_INFO or LM.GUI.ALERT_WARNING,
		string.format("AutoRig Phase 4.1 IK: %d operations, %d rejected. Try dragging the targets now.", #report.operations, failed))
end
