-- **************************************************
-- AutoRig Studio - Phase 0 Moho rig builder
-- GENERATED FILE - edit src/autorig/data/templates/phase0_build.lua and
-- regenerate with `python -m autorig build-lua` instead of editing this copy.
--
-- Target: Moho Pro 14 (embedded Lua 5.4). Menu script.
-- What it does (in the CURRENT document, at frame 0):
--   1. creates a bone layer with the spec's bone hierarchy
--   2. creates one image layer per art part inside that bone layer
--   3. binds each image layer to its bone (layer binding)
--   4. keys the test animation on the bones' angle channels
--   5. reads everything back from Moho and writes a JSON report that
--      `python -m autorig verify-report` checks against the rig spec
--
-- API policy: only Moho API members listed in
-- src/autorig/data/moho_api_allowlist.json may be used here. The cloud test
-- suite runs this script against a strict mock that rejects anything else.
-- **************************************************

ScriptName = "AutoRig_Phase0_Build"

AutoRig_Phase0_Build = {}

-- Rig data generated from the rig spec (all geometry precomputed in Python).
AutoRig_Phase0_Build.DATA = {
	generator = {
		name = "autorig",
		version = "0.0.1",
		spec_character_id = "phase0_test_character",
		spec_sha256 = "93966beab7eedc481f6a8c6a629ab90a0e3c9478fdaaa5f3754ef09b483dc416",
	},
	asset_root = "",
	report_path = "",
	canvas = {
		width = 1920,
		height = 1080,
	},
	bone_layer_name = "AutoRig_Phase0",
	rest_frame = 0,
	start_frame = 1,
	end_frame = 36,
	bones = {
		{
			name = "torso",
			pos = { 0.0, -0.48148148148148145 },
			angle = 1.5707963267948966,
			length = 0.611111111111111,
			world_base = { 0.0, -0.48148148148148145 },
			world_tip = { 0.0, 0.12962962962962962 },
		},
		{
			name = "head",
			parent = "torso",
			pos = { 0.611111111111111, 0.0 },
			angle = 0.0,
			length = 0.4907407407407407,
			world_base = { 0.0, 0.12962962962962962 },
			world_tip = { 0.0, 0.6203703703703703 },
		},
		{
			name = "upper_arm",
			parent = "torso",
			pos = { 0.5185185185185185, -0.18518518518518515 },
			angle = -2.0344439357957027,
			length = 0.37267799624996495,
			world_base = { 0.18518518518518517, 0.037037037037037035 },
			world_tip = { 0.5185185185185185, -0.12962962962962962 },
		},
		{
			name = "forearm",
			parent = "upper_arm",
			pos = { 0.3726779962499649, 0.0 },
			angle = 0.46364760900080615,
			length = 0.3148148148148149,
			world_base = { 0.5185185185185185, -0.12962962962962962 },
			world_tip = { 0.8333333333333334, -0.12962962962962962 },
		},
	},
	layers = {
		{
			name = "torso_art",
			part = "torso",
			bone = "torso",
			image_rel = "art/torso.png",
			image_path = "",
		},
		{
			name = "head_art",
			part = "head",
			bone = "head",
			image_rel = "art/head.png",
			image_path = "",
		},
		{
			name = "upper_arm_art",
			part = "upper_arm",
			bone = "upper_arm",
			image_rel = "art/upper_arm.png",
			image_path = "",
		},
		{
			name = "forearm_art",
			part = "forearm",
			bone = "forearm",
			image_rel = "art/forearm.png",
			image_path = "",
		},
	},
	angle_keys = {
		{
			bone = "torso",
			keys = {
				{
					frame = 1,
					value = 1.5707963267948966,
				},
				{
					frame = 10,
					value = 1.6231562043547265,
				},
				{
					frame = 19,
					value = 1.5707963267948966,
				},
				{
					frame = 28,
					value = 1.5184364492350666,
				},
				{
					frame = 36,
					value = 1.5707963267948966,
				},
			},
		},
		{
			bone = "head",
			keys = {
				{
					frame = 1,
					value = 0.0,
				},
				{
					frame = 10,
					value = -0.10471975511965978,
				},
				{
					frame = 19,
					value = 0.0,
				},
				{
					frame = 28,
					value = 0.13962634015954636,
				},
				{
					frame = 36,
					value = 0.0,
				},
			},
		},
		{
			bone = "upper_arm",
			keys = {
				{
					frame = 1,
					value = -2.0344439357957027,
				},
				{
					frame = 10,
					value = -1.3363122349979708,
				},
				{
					frame = 19,
					value = -1.9471774731959863,
				},
				{
					frame = 28,
					value = -1.3363122349979708,
				},
				{
					frame = 36,
					value = -2.0344439357957027,
				},
			},
		},
		{
			bone = "forearm",
			keys = {
				{
					frame = 1,
					value = 0.46364760900080615,
				},
				{
					frame = 10,
					value = 1.2490457723982544,
				},
				{
					frame = 19,
					value = 0.7254469967999555,
				},
				{
					frame = 28,
					value = 1.2490457723982544,
				},
				{
					frame = 36,
					value = 0.46364760900080615,
				},
			},
		},
	},
}

local SELF = AutoRig_Phase0_Build
local LOG_PREFIX = "[AutoRig] "

function AutoRig_Phase0_Build:Name()
	return "AutoRig Phase 0: Build Test Rig"
end

function AutoRig_Phase0_Build:Version()
	return "0.0.1"
end

function AutoRig_Phase0_Build:Description()
	return "Builds the AutoRig Studio Phase 0 test rig (bones, image layers, bindings, 36-frame animation) and writes a verification report."
end

function AutoRig_Phase0_Build:Creator()
	return "AutoRig Studio"
end

function AutoRig_Phase0_Build:UILabel()
	return "AutoRig Phase 0: Build Test Rig"
end

function AutoRig_Phase0_Build:IsEnabled(moho)
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

local function file_exists(path)
	local f = io.open(path, "rb")
	if f then
		f:close()
		return true
	end
	return false
end

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

-- **************************************************
-- Preflight: refuse to touch a document we did not expect
-- **************************************************

local function preflight(moho, D, report)
	local errors = report.errors
	local doc = moho.document
	if doc == nil then
		errors[#errors + 1] = "No document is open. Create a new document first (File > New)."
		return false
	end
	if D.asset_root == nil or D.asset_root == "" then
		errors[#errors + 1] = "This copy of the script has no asset path baked in (reference build). Generate a machine-specific copy with tools/macos/install_phase0.sh or `python -m autorig build-lua --asset-root ...`."
		return false
	end
	if doc:LayerByName(D.bone_layer_name) ~= nil then
		errors[#errors + 1] = "A layer named '" .. D.bone_layer_name .. "' already exists. Run this script on a fresh document."
	end
	local w, h = doc:Width(), doc:Height()
	report.document_before = { width = w, height = h }
	if w ~= D.canvas.width or h ~= D.canvas.height then
		errors[#errors + 1] = string.format(
			"Project dimensions are %dx%d but the art was made for %dx%d. Set File > Project Settings > Dimensions to %dx%d and run again.",
			w, h, D.canvas.width, D.canvas.height, D.canvas.width, D.canvas.height)
	end
	for _, L in ipairs(D.layers) do
		if not file_exists(L.image_path) then
			errors[#errors + 1] = "Image not found: " .. L.image_path
		end
	end
	return #errors == 0
end

-- **************************************************
-- Build
-- **************************************************

local function build(moho, D, ctx)
	local doc = moho.document
	moho:SetCurFrame(D.rest_frame)

	-- 1. bone layer
	log("Creating bone layer '" .. D.bone_layer_name .. "'")
	local boneLayerGeneric = moho:CreateNewLayer(MOHO.LT_BONE, false)
	boneLayerGeneric:SetName(D.bone_layer_name)
	local boneLayer = moho:LayerAsBone(boneLayerGeneric)
	local skel = boneLayer:Skeleton()
	ctx.boneLayer = boneLayerGeneric
	ctx.skel = skel

	-- 2. bones, parent first (spec order); bone ID == creation index
	ctx.boneIds = {}
	for i, B in ipairs(D.bones) do
		local bone = skel:AddBone(D.rest_frame)
		local id = skel:BoneID(bone)
		if id ~= i - 1 then
			ctx.warnings[#ctx.warnings + 1] = string.format("Bone '%s' got ID %d, expected %d", B.name, id, i - 1)
		end
		bone:SetName(B.name)
		local parentId = -1
		if B.parent ~= nil then
			parentId = ctx.boneIds[B.parent]
		end
		bone.fParent = parentId
		bone.fAnimParent:SetValue(D.rest_frame, parentId)
		bone.fPos:Set(B.pos[1], B.pos[2])
		bone.fAnimPos:SetValue(D.rest_frame, bone.fPos)
		bone.fLength = B.length
		bone.fAngle = B.angle
		bone.fAnimAngle:SetValue(D.rest_frame, B.angle)
		skel:UpdateBoneMatrix(id)
		ctx.boneIds[B.name] = id
		log(string.format("  bone %d '%s' parent=%d pos=(%.4f, %.4f) angle=%.4f rad length=%.4f",
			id, B.name, parentId, B.pos[1], B.pos[2], B.angle, B.length))
	end

	-- 3. image layers, back to front, moved into the bone layer and bound
	ctx.imageLayers = {}
	for _, L in ipairs(D.layers) do
		log("Creating image layer '" .. L.name .. "' <- " .. L.image_path)
		local layer = moho:CreateNewLayer(MOHO.LT_IMAGE, false)
		layer:SetName(L.name)
		moho:LayerAsImage(layer):SetSourceImage(L.image_path)
		moho:PlaceLayerInGroup(layer, boneLayerGeneric, true, false)
		local boneId = ctx.boneIds[L.bone]
		layer:SetLayerParentBone(boneId)
		ctx.imageLayers[#ctx.imageLayers + 1] = { layer = layer, spec = L, bone_id = boneId }
		log(string.format("  bound '%s' -> bone %d '%s'", L.name, boneId, L.bone))
	end

	-- 4. animation keys on the bones' angle channels (radians, parent-relative)
	for _, T in ipairs(D.angle_keys) do
		local bone = skel:Bone(ctx.boneIds[T.bone])
		for _, K in ipairs(T.keys) do
			bone.fAnimAngle:SetValue(K.frame, K.value)
		end
		log(string.format("  keyed %d angle keys on '%s'", #T.keys, T.bone))
	end

	doc:SetStartFrame(D.start_frame)
	doc:SetEndFrame(D.end_frame)
	moho:SetCurFrame(D.rest_frame)
end

-- **************************************************
-- Read back what Moho actually holds (for verify-report)
-- **************************************************

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
	local okChildren, errChildren = pcall(function()
		local group = moho:LayerAsGroup(bl)
		for i = 0, group:CountLayers() - 1 do
			local child = group:Layer(i)
			report.bone_layer.children_by_index[#report.bone_layer.children_by_index + 1] = child:Name()
		end
	end)
	if not okChildren then
		ctx.warnings[#ctx.warnings + 1] = "Could not read bone layer children: " .. tostring(errChildren)
	end

	local skel = ctx.skel
	report.bones = SELF.array()
	for id = 0, skel:CountBones() - 1 do
		local bone = skel:Bone(id)
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
		report.bones[#report.bones + 1] = entry
	end

	report.layers = SELF.array()
	for _, item in ipairs(ctx.imageLayers) do
		local layer = item.layer
		report.layers[#report.layers + 1] = {
			name = layer:Name(),
			type = layer:LayerType(),
			parent_bone_id = layer:LayerParentBone(),
			source_image_set = item.spec.image_path,
		}
	end
end

-- **************************************************
-- Entry point
-- **************************************************

function AutoRig_Phase0_Build:Run(moho)
	local D = SELF.DATA
	local report = {
		autorig_report_version = "0.1.0",
		status = "started",
		generator = D.generator,
		lua_version = _VERSION,
		errors = SELF.array(),
		warnings = SELF.array(),
	}
	local okVersion, appVersion = pcall(function() return moho:AppVersion() end)
	report.moho_app_version = okVersion and appVersion or ("unavailable: " .. tostring(appVersion))
	log("Phase 0 build starting (" .. _VERSION .. ", Moho " .. tostring(report.moho_app_version) .. ")")

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

	if not preflight(moho, D, report) then
		report.status = "aborted"
		for _, e in ipairs(report.errors) do log("ERROR: " .. e) end
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 0: nothing was changed.")
		return
	end

	local ctx = { warnings = report.warnings }
	local okBuild, errBuild = pcall(build, moho, D, ctx)
	if not okBuild then
		report.status = "failed"
		report.errors[#report.errors + 1] = "Build failed (Moho API error?): " .. tostring(errBuild)
		log("ERROR: " .. tostring(errBuild))
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 0: build FAILED - close this document without saving.")
		return
	end

	local okRead, errRead = pcall(readback, moho, D, ctx, report)
	if not okRead then
		report.status = "failed"
		report.errors[#report.errors + 1] = "Read-back failed: " .. tostring(errRead)
		log("ERROR: " .. tostring(errRead))
		finish(LM.GUI.ALERT_WARNING, "AutoRig Phase 0: rig built but read-back FAILED.")
		return
	end

	report.status = "completed"
	moho:SetSelLayer(ctx.boneLayer)
	moho.document:SetDirty()
	moho:UpdateUI()
	for _, w in ipairs(report.warnings) do log("WARNING: " .. w) end
	log("Phase 0 build completed.")
	finish(LM.GUI.ALERT_INFO, "AutoRig Phase 0: test rig built. Run verify-report on the JSON report.")
end
