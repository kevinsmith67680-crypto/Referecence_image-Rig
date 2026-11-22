-- **************************************************
-- AutoRig Studio - Phase 4.0 native proof: verify the REOPENED rig, move it again
-- GENERATED FILE - edit src/autorig/data/templates/phase4_native_verify.lua and
-- regenerate with `python -m autorig native-poc` instead of editing this copy.
--
-- Target: Moho Pro 14 / 14.5 (embedded Lua 5.4). Menu script.
-- Run it on the document that the Phase 4.0 build script created, AFTER that
-- document was saved, closed and opened again. It:
--   1. finds the bone layer by name and reads back the bones, the image layers,
--      their bindings and the angle keys exactly as the reopened file holds them
--   2. moves the arm bone again: a NEW angle key at a frame the build did not
--      key, read back from the channel, then makes that frame current
--   3. lists (read-only, without calling anything) the ScriptInterface members
--      whose names look file- or render-related, for the Phase 4.1 decision on
--      automating save/reopen/render
--   4. writes a JSON report that `python -m autorig native-verify` checks
--
-- API policy: only Moho API members listed in
-- src/autorig/data/moho_api_allowlist.json may be called here. The cloud test
-- suite runs this script against a strict mock that rejects anything else.
-- **************************************************

ScriptName = "AutoRig_P4_NativeVerify"

AutoRig_P4_NativeVerify = {}

-- @@AUTORIG_DATA@@

local SELF = AutoRig_P4_NativeVerify
local LOG_PREFIX = "[AutoRig P4] "
local SURFACE_WORDS = { "file", "save", "open", "close", "new", "revert", "render", "export", "quit" }

function AutoRig_P4_NativeVerify:Name()
	return "AutoRig Phase 4.0: Verify Reopened Rig"
end

function AutoRig_P4_NativeVerify:Version()
	return "0.1.0"
end

function AutoRig_P4_NativeVerify:Description()
	return "Checks the reopened AutoRig Phase 4.0 native proof rig, keys the arm at a new frame and writes a verification report."
end

function AutoRig_P4_NativeVerify:Creator()
	return "AutoRig Studio"
end

function AutoRig_P4_NativeVerify:UILabel()
	return "AutoRig Phase 4.0: Verify Reopened Rig"
end

function AutoRig_P4_NativeVerify:IsEnabled(moho)
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
local function file_related_names(obj)
	local found, seen, notes = {}, {}, SELF.array()
	local function add_keys(t, depth)
		if type(t) ~= "table" or seen[t] or depth > 6 then return end
		seen[t] = true
		for k in pairs(t) do
			if type(k) == "string" and string.sub(k, 1, 2) ~= "__" then
				local lname = string.lower(k)
				for _, w in ipairs(SURFACE_WORDS) do
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
SELF.file_related_names = file_related_names

-- **************************************************
-- Read back the reopened rig
-- **************************************************

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
end

-- **************************************************
-- Move the arm again: a new key at a new frame, read back
-- **************************************************

local function move_again(moho, D, ctx, report)
	local M = D.move
	local id = ctx.boneIds[M.bone]
	if id == nil then
		error("bone '" .. M.bone .. "' not found in the reopened skeleton")
	end
	local bone = ctx.skel:Bone(id)
	local ch = bone.fAnimAngle
	local before = ch:GetValue(M.frame)
	ch:SetValue(M.frame, M.value)
	local after = ch:GetValue(M.frame)
	local keys = SELF.array()
	for k = 0, ch:CountKeys() - 1 do
		local when = ch:GetKeyWhen(k)
		keys[#keys + 1] = SELF.array({ when, ch:GetValue(when) })
	end
	moho:SetCurFrame(M.frame)
	-- Informational only: whether bone fields follow the current frame is not
	-- evidenced; the render of this frame is the gate evidence.
	local base = vec(0, 0)
	bone.fMovedMatrix:Transform(base)
	local tip = vec(bone.fLength, 0)
	bone.fMovedMatrix:Transform(tip)
	report.move = {
		bone = M.bone,
		bone_id = id,
		frame = M.frame,
		offset_deg = M.offset_deg,
		value_set = M.value,
		value_before = before,
		value_read = after,
		keys_after = keys,
		observed_after_set_cur_frame = {
			angle = bone.fAngle,
			world_base = SELF.array({ base.x, base.y }),
			world_tip = SELF.array({ tip.x, tip.y }),
		},
	}
	log(string.format("  keyed '%s' at frame %d: %.6f rad (was %.6f, read back %.6f)",
		M.bone, M.frame, M.value, before, after))
end

-- **************************************************
-- Entry point
-- **************************************************

function AutoRig_P4_NativeVerify:Run(moho)
	local D = SELF.DATA
	local report = {
		autorig_report_version = "0.1.0",
		kind = "phase4_native_verify",
		status = "started",
		generator = D.generator,
		lua_version = _VERSION,
		errors = SELF.array(),
		warnings = SELF.array(),
	}
	local okVersion, appVersion = pcall(function() return moho:AppVersion() end)
	report.moho_app_version = okVersion and appVersion or ("unavailable: " .. tostring(appVersion))
	log("Phase 4.0 verify starting (" .. _VERSION .. ", Moho " .. tostring(report.moho_app_version) .. ")")

	local function finish(kind, title)
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

	report.api_surface = { ScriptInterface = file_related_names(moho) }

	local doc = moho.document
	if doc == nil then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "No document is open. Open the saved Phase 4.0 .moho file first."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.0 verify: nothing was changed.")
		return
	end
	if D.report_path == nil or D.report_path == "" then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "This copy of the script has no report path baked in (reference build). Generate it with tools/macos/phase4_native_test.sh."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.0 verify: nothing was changed.")
		return
	end
	local layer = doc:LayerByName(D.bone_layer_name)
	if layer == nil then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "No layer named '" .. D.bone_layer_name .. "' in this document. Open the .moho file saved after the Phase 4.0 build."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.0 verify: rig not found.")
		return
	end
	if layer:LayerType() ~= MOHO.LT_BONE then
		report.status = "aborted"
		report.errors[#report.errors + 1] = "Layer '" .. D.bone_layer_name .. "' is not a bone layer."
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.0 verify: rig not found.")
		return
	end

	local ctx = { boneLayer = layer }
	ctx.skel = moho:LayerAsBone(layer):Skeleton()

	moho:SetCurFrame(D.rest_frame)
	local okRead, errRead = pcall(readback, moho, D, ctx, report)
	if not okRead then
		report.status = "failed"
		report.errors[#report.errors + 1] = "Read-back failed: " .. tostring(errRead)
		log("ERROR: " .. tostring(errRead))
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.0 verify: read-back FAILED.")
		return
	end

	local okMove, errMove = pcall(move_again, moho, D, ctx, report)
	if not okMove then
		report.status = "failed"
		report.errors[#report.errors + 1] = "Moving the reopened bone failed: " .. tostring(errMove)
		log("ERROR: " .. tostring(errMove))
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 4.0 verify: move FAILED.")
		return
	end

	report.status = "completed"
	moho:SetSelLayer(layer)
	doc:SetDirty()
	moho:UpdateUI()
	for _, w in ipairs(report.warnings) do log("WARNING: " .. w) end
	log("Phase 4.0 verify completed.")
	finish(LM.GUI.ALERT_INFO, string.format("AutoRig Phase 4.0: rig found and '%s' keyed at frame %d. Save (Cmd+S) now.",
		D.move.bone, D.move.frame))
end
