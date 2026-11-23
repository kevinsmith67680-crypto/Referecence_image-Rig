-- **************************************************
-- AutoRig Studio - Phase 4.1 full-body rig: verify the REOPENED rig, edit it again
-- GENERATED FILE - edit src/autorig/data/templates/phase4_1_fullbody_verify.lua and
-- regenerate with `python -m autorig native-fullbody` instead of editing this copy.
--
-- Target: Moho Pro 14.5 (embedded Lua 5.4). Menu script. Run it on the full-body rig document AFTER it was
-- saved, closed and opened again. It:
--   1. finds the bone layer by name and reads back every bone (parent, rest values, keys), every image layer
--      and its binding, at the rest frame
--   2. reads the optional IK / constraint fields (fTargetBone, fConstraints, fMinConstraint, fMaxConstraint)
--      inside pcall: they are allowlisted from documentation but not yet run natively
--   3. edits the reopened rig: position keys that shift the pelvis (test pose 9), read back from the channel
--   4. lists, without calling anything, the bone and ScriptInterface member names that look IK-, constraint-,
--      file- or render-related (discovery for the next phases)
--   5. writes a JSON report that `python -m autorig native-fullbody-verify` checks
--
-- API policy: only members listed in src/autorig/data/moho_api_allowlist.json; the cloud suite runs this
-- script against a strict mock that rejects anything else.
-- **************************************************

ScriptName = "AutoRig_P41_FullBodyVerify"

AutoRig_P41_FullBodyVerify = {}

-- @@AUTORIG_DATA@@

local SELF = AutoRig_P41_FullBodyVerify
local LOG_PREFIX = "[AutoRig P4.1] "
local FILE_WORDS = { "file", "save", "open", "close", "new", "revert", "render", "export", "quit" }
local BONE_WORDS = { "target", "constraint", "ik", "strength", "fixed", "lock", "independent", "angle", "scale",
	"flip", "smart", "dynamics", "physics" }

function AutoRig_P41_FullBodyVerify:Name()
	return "AutoRig Phase 4.1: Verify Reopened Full-Body Rig"
end

function AutoRig_P41_FullBodyVerify:Version()
	return "0.1.0"
end

function AutoRig_P41_FullBodyVerify:Description()
	return "Checks the reopened AutoRig Phase 4.1 full-body rig, shifts the pelvis with new keys and writes a verification report."
end

function AutoRig_P41_FullBodyVerify:Creator()
	return "AutoRig Studio"
end

function AutoRig_P41_FullBodyVerify:UILabel()
	return "AutoRig Phase 4.1: Verify Reopened Full-Body Rig"
end

function AutoRig_P41_FullBodyVerify:IsEnabled(moho)
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
-- Read back the reopened rig
-- **************************************************

local function optional(fn)
	local ok, v = pcall(fn)
	if ok then return v end
	return "unavailable: " .. tostring(v)
end

local function optional_fields(bone)
	return {
		constraints = optional(function() return bone.fConstraints end),
		min_constraint = optional(function() return bone.fMinConstraint end),
		max_constraint = optional(function() return bone.fMaxConstraint end),
		target_bone = optional(function() return bone.fTargetBone:GetValue(0) end),
	}
end

local function read_bone(bone, id)
	local entry = {
		id = id,
		name = bone:Name(),
		parent_id = bone.fParent,
		pos = SELF.array({ bone.fPos.x, bone.fPos.y }),
		angle = bone.fAngle,
		length = bone.fLength,
		angle_keys = SELF.array(),
	}
	local base = vec(0, 0)
	bone.fMovedMatrix:Transform(base)
	local tip = vec(bone.fLength, 0)
	bone.fMovedMatrix:Transform(tip)
	entry.world_base = SELF.array({ base.x, base.y })
	entry.world_tip = SELF.array({ tip.x, tip.y })
	local ch = bone.fAnimAngle
	for k = 0, ch:CountKeys() - 1 do
		local when = ch:GetKeyWhen(k)
		entry.angle_keys[#entry.angle_keys + 1] = SELF.array({ when, ch:GetValue(when) })
	end
	entry.optional = optional_fields(bone)
	return entry
end

local function readback(moho, D, ctx, report)
	local doc = moho.document
	report.document = {
		width = doc:Width(),
		height = doc:Height(),
		fps = doc:Fps(),
		start_frame = doc:StartFrame(),
		end_frame = doc:EndFrame(),
	}
	report.constants = { LT_BONE = MOHO.LT_BONE, LT_IMAGE = MOHO.LT_IMAGE }
	local bl = ctx.boneLayer
	report.bone_layer = { name = bl:Name(), type = bl:LayerType(), children_by_index = SELF.array() }
	report.layers = SELF.array()
	local group = moho:LayerAsGroup(bl)
	for i = 0, group:CountLayers() - 1 do
		local child = group:Layer(i)
		report.bone_layer.children_by_index[#report.bone_layer.children_by_index + 1] = child:Name()
		report.layers[#report.layers + 1] = {
			name = child:Name(),
			type = child:LayerType(),
			parent_bone_id = child:LayerParentBone(),
		}
	end
	local skel = ctx.skel
	report.bones = SELF.array()
	ctx.boneIds = {}
	for id = 0, skel:CountBones() - 1 do
		local bone = skel:Bone(id)
		local entry = read_bone(bone, id)
		ctx.boneIds[entry.name] = id
		report.bones[#report.bones + 1] = entry
	end
	if skel:CountBones() > 0 then
		report.api_surface.M_Bone = names_matching(skel:Bone(0), BONE_WORDS)
	end
end

-- **************************************************
-- Edit the reopened rig: new position keys, read back
-- **************************************************

local function apply_moves(moho, D, ctx, report)
	report.moves = SELF.array()
	for _, M in ipairs(D.moves) do
		local id = ctx.boneIds[M.bone]
		if id == nil then
			error("bone '" .. M.bone .. "' not found in the reopened skeleton")
		end
		local bone = ctx.skel:Bone(id)
		local entry = { bone = M.bone, bone_id = id, kind = M.kind, keys = SELF.array() }
		for _, K in ipairs(M.keys) do
			if M.kind == "pos" then
				bone.fAnimPos:SetValue(K.frame, vec(K.x, K.y))
				local got = bone.fAnimPos:GetValue(K.frame)
				entry.keys[#entry.keys + 1] = { frame = K.frame, set = SELF.array({ K.x, K.y }),
					read = SELF.array({ got.x, got.y }) }
			else
				bone.fAnimAngle:SetValue(K.frame, K.value)
				entry.keys[#entry.keys + 1] = { frame = K.frame, set = K.value, read = bone.fAnimAngle:GetValue(K.frame) }
			end
		end
		report.moves[#report.moves + 1] = entry
		log(string.format("  keyed %d %s keys on '%s'", #M.keys, M.kind, M.bone))
	end
end

-- **************************************************
-- Entry point
-- **************************************************

function AutoRig_P41_FullBodyVerify:Run(moho)
	local D = SELF.DATA
	local t0 = os.clock()
	local report = {
		autorig_report_version = "0.1.0",
		kind = "phase4_1_fullbody_verify",
		status = "started",
		generator = D.generator,
		lua_version = _VERSION,
		errors = SELF.array(),
		warnings = SELF.array(),
		timings_s = {},
		api_surface = {},
	}
	local okVersion, appVersion = pcall(function() return moho:AppVersion() end)
	report.moho_app_version = okVersion and appVersion or ("unavailable: " .. tostring(appVersion))
	log("Phase 4.1 verify starting (" .. _VERSION .. ", Moho " .. tostring(report.moho_app_version) .. ")")

	local function finish(kind, title)
		report.timings_s.total_cpu = os.clock() - t0
		if D.report_path ~= nil and D.report_path ~= "" then
			local ok, err = write_text(D.report_path, json_encode(report) .. "\n")
			if ok then
				log("Report written: " .. D.report_path)
			else
				log("Could not write report " .. D.report_path .. ": " .. err)
			end
		end
		local detail = report.errors[1] or report.warnings[1] or ("Report: " .. tostring(D.report_path))
		alert(kind, title, detail, nil)
	end

	report.api_surface.ScriptInterface = names_matching(moho, FILE_WORDS)

	local doc = moho.document
	if doc == nil then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "No document is open. Open the saved Phase 4.1 .moho file first."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 verify: nothing was changed.")
		return
	end
	if D.report_path == nil or D.report_path == "" then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "This copy of the script has no report path baked in (reference build). Generate it with tools/macos/phase4_native_test.sh --phase 4.1."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 verify: nothing was changed.")
		return
	end
	local layer = doc:LayerByName(D.bone_layer_name)
	if layer == nil or layer:LayerType() ~= MOHO.LT_BONE then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "No bone layer named '" .. D.bone_layer_name .. "' in this document. Open the .moho file saved after the Phase 4.1 build."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 verify: rig not found.")
		return
	end

	local ctx = { boneLayer = layer }
	ctx.skel = moho:LayerAsBone(layer):Skeleton()
	moho:SetCurFrame(D.rest_frame)
	local t1 = os.clock()
	local okRead, errRead = pcall(readback, moho, D, ctx, report)
	report.timings_s.readback_cpu = os.clock() - t1
	if not okRead then
		report.status = "failed"
		report.errors[#report.errors + 1] = "Read-back failed: " .. tostring(errRead)
		log("ERROR: " .. tostring(errRead))
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 verify: read-back FAILED.")
		return
	end
	local okMove, errMove = pcall(apply_moves, moho, D, ctx, report)
	if not okMove then
		report.status = "failed"
		report.errors[#report.errors + 1] = "Editing the reopened rig failed: " .. tostring(errMove)
		log("ERROR: " .. tostring(errMove))
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.1 verify: edit FAILED.")
		return
	end
	report.status = "completed"
	moho:SetCurFrame(D.rest_frame)
	moho:SetSelLayer(layer)
	doc:SetDirty()
	moho:UpdateUI()
	log("Phase 4.1 verify completed.")
	finish(LM.GUI.ALERT_INFO, "AutoRig Phase 4.1: reopened rig verified and the pelvis shift keyed. Save (Cmd+S) now.")
end
