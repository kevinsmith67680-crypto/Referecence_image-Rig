-- mock_moho.lua - STRICT mock of the Moho Pro 14 Lua API subset that AutoRig
-- is allowed to use (src/autorig/data/moho_api_allowlist.json).
--
-- * Any read/write/call of a member that is not implemented here raises
--   "UNSUPPORTED MOHO API: <Owner>.<member>". Tests assert that the set of
--   implemented members equals the allowlist exactly, so the generated
--   scripts cannot quietly start depending on an undocumented function.
-- * Semantics follow the documented/observed behaviour cited in
--   docs/moho-lua-api-evidence.md (e.g. child fPos in the parent's local
--   frame). Where Moho's real behaviour is unknown the mock is deliberately
--   simple; passing against this mock is NOT evidence that Moho behaves
--   the same - that is what the native (on-Mac) test is for.
--
-- Usage (from Python via lupa, or plain Lua 5.4):
--   local mock = dofile("mock_moho.lua")
--   local env = mock.new_env({ width = 1920, height = 1080 })
--   -- globals MOHO and LM are now installed; env.moho is the ScriptInterface

local M = {}

M.registry = {}   -- owner -> { member = "method"|"field"|"constant"|"function" }

local function fail(msg)
	error("UNSUPPORTED MOHO API: " .. msg, 3)
end

local function check(cond, msg)
	if not cond then
		error("MOCK MOHO MISUSE: " .. msg, 3)
	end
end

local function register(owner, name, kind)
	M.registry[owner] = M.registry[owner] or {}
	M.registry[owner][name] = kind
end

-- A "class" = own methods + allowed fields + optional parent classes.
local function class(owner, methods, fields, parents)
	for k in pairs(methods) do register(owner, k, "method") end
	for k in pairs(fields or {}) do register(owner, k, "field") end
	local cls = { owner = owner, methods = methods, fields = fields or {}, parents = parents or {} }
	local function find_method(c, key)
		local m = c.methods[key]
		if m ~= nil then return m end
		for _, p in ipairs(c.parents) do
			local pm = find_method(p, key)
			if pm ~= nil then return pm end
		end
		return nil
	end
	local function has_field(c, key)
		if c.fields[key] then return true end
		for _, p in ipairs(c.parents) do
			if has_field(p, key) then return true end
		end
		return false
	end
	cls.mt = {
		__index = function(obj, key)
			local m = find_method(cls, key)
			if m ~= nil then return m end
			if has_field(cls, key) then return rawget(obj, "_f")[key] end
			fail(owner .. "." .. tostring(key))
		end,
		__newindex = function(obj, key, value)
			if has_field(cls, key) then
				rawget(obj, "_f")[key] = value
				return
			end
			fail(owner .. "." .. tostring(key) .. " (assignment)")
		end,
	}
	cls.new = function(state)
		return setmetatable({ _f = {}, _s = state or {} }, cls.mt)
	end
	cls.wrap = function(state, f)
		return setmetatable({ _f = f or {}, _s = state }, cls.mt)
	end
	return cls
end

local function S(obj) return rawget(obj, "_s") end
local function F(obj) return rawget(obj, "_f") end

local function is_int(n)
	return type(n) == "number" and math.type(n) == "integer" or (type(n) == "number" and n == math.floor(n))
end

-- ---------------------------------------------------------------------------
-- LM.Vector2 / LM_Transform
-- ---------------------------------------------------------------------------

local Vector2 = class("LM_Vector2", {
	Set = function(self, a, b)
		if type(a) == "table" then
			self.x, self.y = a.x, a.y
		else
			check(type(a) == "number" and type(b) == "number", "LM_Vector2:Set expects (x, y) or (vec)")
			self.x, self.y = a, b
		end
	end,
}, { x = true, y = true })

local function new_vec(x, y)
	local v = Vector2.new()
	F(v).x, F(v).y = x or 0, y or 0
	return v
end

local Vector2Class = class("LM_Vector2", {
	new_local = function(self) return new_vec(0, 0) end,
})

-- 2x3 affine stored as {a, b, c, d, e, f}: x' = a x + b y + c ; y' = d x + e y + f
local Transform = class("LM_Transform", {
	Transform = function(self, v)
		local m = S(self).m
		local x, y = v.x, v.y
		v.x = m[1] * x + m[2] * y + m[3]
		v.y = m[4] * x + m[5] * y + m[6]
	end,
})

local function mat_mul(p, q)
	return {
		p[1] * q[1] + p[2] * q[4], p[1] * q[2] + p[2] * q[5], p[1] * q[3] + p[2] * q[6] + p[3],
		p[4] * q[1] + p[5] * q[4], p[4] * q[2] + p[5] * q[5], p[4] * q[3] + p[5] * q[6] + p[6],
	}
end

local function mat_pose(x, y, angle)
	local c, s = math.cos(angle), math.sin(angle)
	return { c, -s, x, s, c, y }
end

-- ---------------------------------------------------------------------------
-- Animation channels (AnimVal / AnimVec2 / int channels share one surface)
-- ---------------------------------------------------------------------------

local AnimChannel
AnimChannel = class("AnimChannel", {
	SetValue = function(self, frame, value)
		check(is_int(frame) and frame >= 0, "SetValue frame must be an integer >= 0, got " .. tostring(frame))
		local st = S(self)
		if st.kind == "vec2" then
			check(type(value) == "table", "AnimVec2:SetValue expects an LM_Vector2")
			value = { x = value.x, y = value.y }
		elseif st.kind == "int" then
			check(is_int(value), "int channel SetValue expects an integer")
		else
			check(type(value) == "number", "AnimVal:SetValue expects a number")
		end
		st.keys[frame] = value
	end,
	GetValue = function(self, frame)
		local st = S(self)
		local frames = {}
		for f in pairs(st.keys) do frames[#frames + 1] = f end
		table.sort(frames)
		local v = st.keys[frames[1]]
		for _, f in ipairs(frames) do
			if f <= frame then v = st.keys[f] end
		end
		if st.keys[frame] ~= nil then v = st.keys[frame] end
		if st.kind == "vec2" then return new_vec(v.x, v.y) end
		return v
	end,
	CountKeys = function(self)
		local n = 0
		for _ in pairs(S(self).keys) do n = n + 1 end
		return n
	end,
	GetKeyWhen = function(self, index)
		local frames = {}
		for f in pairs(S(self).keys) do frames[#frames + 1] = f end
		table.sort(frames)
		check(index >= 0 and index < #frames, "GetKeyWhen index out of range")
		return frames[index + 1]
	end,
})

local function new_channel(kind, initial)
	return AnimChannel.new({ kind = kind, keys = { [0] = initial } })
end

-- ---------------------------------------------------------------------------
-- Bones and skeleton
-- ---------------------------------------------------------------------------

local Bone = class("M_Bone", {
	SetName = function(self, name)
		check(type(name) == "string", "M_Bone:SetName expects a string")
		S(self).name = name
	end,
	Name = function(self) return S(self).name end,
}, {
	fParent = true, fAnimParent = true, fPos = true, fAnimPos = true, fLength = true,
	fAngle = true, fAnimAngle = true, fMovedMatrix = true,
	-- Phase 4.1 (opt-in IK/constraints script): documented members, defaults as in a native Moho 14.5.1 file
	fConstraints = true, fMinConstraint = true, fMaxConstraint = true, fTargetBone = true,
})

local function bone_world(skel_state, id, depth)
	depth = depth or 0
	check(depth < 64, "bone parent cycle")
	local b = skel_state.bones[id + 1]
	local f = F(b)
	local local_m = mat_pose(f.fPos.x, f.fPos.y, f.fAngle)
	if f.fParent == -1 then return local_m end
	check(f.fParent >= 0 and f.fParent < #skel_state.bones, "fParent out of range: " .. tostring(f.fParent))
	return mat_mul(bone_world(skel_state, f.fParent, depth + 1), local_m)
end

local Skeleton = class("M_Skeleton", {
	AddBone = function(self, frame)
		check(is_int(frame), "AddBone(frame) expects an integer frame")
		local st = S(self)
		local b = Bone.new({ name = "B" .. (#st.bones + 1) })
		local f = F(b)
		f.fParent = -1
		f.fAnimParent = new_channel("int", -1)
		f.fPos = new_vec(0, 0)
		f.fAnimPos = new_channel("vec2", { x = 0, y = 0 })
		f.fLength = 0.5
		f.fAngle = 0
		f.fAnimAngle = new_channel("num", 0)
		f.fMovedMatrix = Transform.new({ m = { 1, 0, 0, 0, 1, 0 } })
		f.fConstraints = false
		f.fMinConstraint = -1.221731
		f.fMaxConstraint = 1.221731
		f.fTargetBone = new_channel("num", -1)
		st.bones[#st.bones + 1] = b
		return b
	end,
	BoneID = function(self, bone)
		for i, b in ipairs(S(self).bones) do
			if rawequal(b, bone) then return i - 1 end
		end
		return -1
	end,
	Bone = function(self, id)
		local st = S(self)
		check(is_int(id) and id >= 0 and id < #st.bones, "Bone(id) out of range: " .. tostring(id))
		return st.bones[id + 1]
	end,
	CountBones = function(self) return #S(self).bones end,
	UpdateBoneMatrix = function(self, id)
		local st = S(self)
		check(is_int(id) and id >= 0 and id < #st.bones, "UpdateBoneMatrix(id) out of range")
		S(F(st.bones[id + 1]).fMovedMatrix).m = bone_world(st, id)
	end,
})

-- ---------------------------------------------------------------------------
-- Layers. One shared state table per layer; casts return typed views.
-- ---------------------------------------------------------------------------

local LT = { LT_UNKNOWN = 0, LT_VECTOR = 1, LT_IMAGE = 2, LT_GROUP = 3, LT_BONE = 4 }

local Layer, GroupLayer, BoneLayer, ImageLayer

Layer = class("MohoLayer", {
	SetName = function(self, name)
		check(type(name) == "string", "SetName expects a string")
		S(self).name = name
	end,
	Name = function(self) return S(self).name end,
	LayerType = function(self) return S(self).type end,
	SetLayerParentBone = function(self, id)
		local st = S(self)
		check(is_int(id), "SetLayerParentBone expects an integer bone id")
		local parent = st.parent
		check(parent ~= nil and parent.type == LT.LT_BONE,
			"SetLayerParentBone on a layer that is not inside a bone layer")
		check(id == -1 or id == -2 or (id >= 0 and id < #parent.skeleton.bones),
			"SetLayerParentBone id out of range: " .. tostring(id))
		st.parent_bone = id
	end,
	LayerParentBone = function(self) return S(self).parent_bone end,
	IsGroupType = function(self) return S(self).children ~= nil end,
	CountActions = function(self) return #(S(self).actions or {}) end,
	ActionName = function(self, i)
		local acts = S(self).actions or {}
		check(is_int(i) and i >= 0 and i < #acts, "ActionName(i) out of range")
		return acts[i + 1].name
	end,
	IsSmartBoneAction = function(self, name)
		check(type(name) == "string", "IsSmartBoneAction expects an action name")
		for _, a in ipairs(S(self).actions or {}) do
			if a.name == name then return a.smart == true end
		end
		return false
	end,
})

GroupLayer = class("MohoGroupLayer", {
	CountLayers = function(self) return #S(self).children end,
	Layer = function(self, i)
		local st = S(self)
		check(is_int(i) and i >= 0 and i < #st.children, "Layer(i) out of range")
		return Layer.wrap(st.children[i + 1])
	end,
}, nil, { Layer })

BoneLayer = class("MohoBoneLayer", {
	Skeleton = function(self) return Skeleton.wrap(S(self).skeleton) end,
}, nil, { GroupLayer, Layer })

ImageLayer = class("MohoImageLayer", {
	SetSourceImage = function(self, path)
		check(type(path) == "string" and path ~= "", "SetSourceImage expects a non-empty path")
		if M.fail_on == "SetSourceImage" then
			error("simulated Moho failure inside SetSourceImage")
		end
		S(self).source_image = path
	end,
}, nil, { Layer })

-- ---------------------------------------------------------------------------
-- Document and ScriptInterface
-- ---------------------------------------------------------------------------

local function remove_from(list, st)
	for i, x in ipairs(list) do
		if x == st then
			table.remove(list, i)
			return true
		end
	end
	return false
end

local function find_by_name(list, name)
	for _, st in ipairs(list) do
		if st.name == name then return st end
		if st.children then
			local found = find_by_name(st.children, name)
			if found then return found end
		end
	end
	return nil
end

local Doc = class("MohoDoc", {
	Width = function(self) return S(self).width end,
	Height = function(self) return S(self).height end,
	Fps = function(self) return S(self).fps end,
	StartFrame = function(self) return S(self).start_frame end,
	EndFrame = function(self) return S(self).end_frame end,
	SetStartFrame = function(self, f)
		check(is_int(f), "SetStartFrame expects an integer")
		S(self).start_frame = f
	end,
	SetEndFrame = function(self, f)
		check(is_int(f), "SetEndFrame expects an integer")
		S(self).end_frame = f
	end,
	LayerByName = function(self, name)
		local st = find_by_name(S(self).layers, name)
		if st == nil then return nil end
		return Layer.wrap(st)
	end,
	SetDirty = function(self) S(self).dirty = true end,
	CountLayers = function(self) return #S(self).layers end,
	Layer = function(self, i)
		local st = S(self)
		check(is_int(i) and i >= 0 and i < #st.layers, "MohoDoc:Layer(i) out of range")
		return Layer.wrap(st.layers[i + 1])
	end,
})

local ScriptInterface = class("ScriptInterface", {
	CreateNewLayer = function(self, layerType, undoable)
		check(is_int(layerType), "CreateNewLayer expects MOHO.LT_* as first argument")
		check(type(undoable) == "boolean", "CreateNewLayer expects a boolean 'undoable' second argument")
		local env = S(self)
		local st = { name = "Layer " .. (env.next_layer_id), type = layerType, parent_bone = -1, parent = nil }
		env.next_layer_id = env.next_layer_id + 1
		if layerType == LT.LT_BONE or layerType == LT.LT_GROUP then
			st.children = {}
		end
		if layerType == LT.LT_BONE then
			st.skeleton = { bones = {} }
		end
		table.insert(env.doc.layers, st) -- top of the top-level stack
		env.selected = st
		env.log[#env.log + 1] = "CreateNewLayer " .. layerType
		return Layer.wrap(st)
	end,
	PlaceLayerInGroup = function(self, child, group, top, undoable)
		local cst, gst = S(child), S(group)
		check(type(cst) == "table" and type(gst) == "table", "PlaceLayerInGroup expects two layers")
		check(gst.children ~= nil, "PlaceLayerInGroup target is not a group-type layer")
		check(type(top) == "boolean", "PlaceLayerInGroup 'top' must be boolean")
		check(type(undoable) == "boolean", "PlaceLayerInGroup 'isUndoable' must be boolean")
		local env = S(self)
		local container = cst.parent and cst.parent.children or env.doc.layers
		check(remove_from(container, cst), "child layer not found in its container")
		if top then
			table.insert(gst.children, cst) -- mock convention: index 0 = bottom, last = top
		else
			table.insert(gst.children, 1, cst)
		end
		cst.parent = gst
	end,
	LayerAsBone = function(self, layer)
		local st = S(layer)
		check(st.type == LT.LT_BONE, "LayerAsBone on a non-bone layer")
		return BoneLayer.wrap(st)
	end,
	LayerAsImage = function(self, layer)
		local st = S(layer)
		check(st.type == LT.LT_IMAGE, "LayerAsImage on a non-image layer")
		return ImageLayer.wrap(st)
	end,
	LayerAsGroup = function(self, layer)
		local st = S(layer)
		check(st.children ~= nil, "LayerAsGroup on a non-group layer")
		return GroupLayer.wrap(st)
	end,
	SetCurFrame = function(self, f)
		check(is_int(f), "SetCurFrame expects an integer")
		S(self).frame = f
	end,
	SetSelLayer = function(self, layer) S(self).selected = S(layer) end,
	UpdateUI = function(self) S(self).ui_updates = S(self).ui_updates + 1 end,
	AppVersion = function(self) return S(self).app_version end,
}, { document = true })

-- ---------------------------------------------------------------------------
-- Globals MOHO and LM (strict tables)
-- ---------------------------------------------------------------------------

local function strict_table(owner, members)
	for k, v in pairs(members) do
		register(owner, k, type(v) == "function" and "function" or "constant")
	end
	return setmetatable({}, {
		__index = function(_, key)
			local v = members[key]
			if v == nil then fail(owner .. "." .. tostring(key)) end
			return v
		end,
		__newindex = function(_, key) fail(owner .. "." .. tostring(key) .. " (assignment)") end,
	})
end

function M.new_env(opts)
	opts = opts or {}
	M.fail_on = opts.fail_on
	local env = {
		doc = {
			width = opts.width or 1920,
			height = opts.height or 1080,
			fps = opts.fps or 24,
			start_frame = 1,
			end_frame = 72,
			layers = {},
			dirty = false,
		},
		frame = opts.frame or 5,
		next_layer_id = 1,
		selected = nil,
		ui_updates = 0,
		alerts = {},
		log = {},
		app_version = opts.app_version or "14.4-mock",
	}
	-- a fresh Moho document starts with one vector layer
	local actions = {}
	for i, a in ipairs(opts.actions or {}) do
		actions[i] = { name = a.name, smart = a.smart == true }
	end
	table.insert(env.doc.layers, { name = "Layer 1", type = LT.LT_VECTOR, parent_bone = -1, actions = actions })
	if opts.preexisting_layer then
		table.insert(env.doc.layers, { name = opts.preexisting_layer, type = LT.LT_GROUP, parent_bone = -1, children = {} })
	end

	local gui = strict_table("LM.GUI", {
		ALERT_INFO = 1,
		ALERT_WARNING = 2,
		Alert = function(kind, a, b, c, b1, b2, b3)
			env.alerts[#env.alerts + 1] = { kind = kind, line1 = a, line2 = b, line3 = c, button = b1 }
			return 0
		end,
	})
	_G.LM = setmetatable({}, {
		__index = function(_, key)
			if key == "Vector2" then return Vector2Class.new({}) end
			if key == "GUI" then return gui end
			fail("LM." .. tostring(key))
		end,
		__newindex = function(_, key) fail("LM." .. tostring(key) .. " (assignment)") end,
	})
	_G.MOHO = strict_table("MOHO", {
		LT_BONE = LT.LT_BONE,
		LT_IMAGE = LT.LT_IMAGE,
		Localize = function(s)
			return (tostring(s):match("=(.*)$")) or tostring(s)
		end,
	})

	local moho = ScriptInterface.new(env)
	F(moho).document = Doc.new(env.doc)
	env.moho = moho
	return env
end

-- Convenience for Python tests: flatten state into plain tables.
function M.snapshot(env)
	local function layer_snapshot(st)
		local out = { name = st.name, type = st.type, parent_bone = st.parent_bone, source_image = st.source_image }
		if st.children then
			out.children = {}
			for i, c in ipairs(st.children) do out.children[i] = layer_snapshot(c) end
		end
		if st.skeleton then
			out.bones = {}
			for i, b in ipairs(st.skeleton.bones) do
				local f = F(b)
				local keys = {}
				for frame, v in pairs(S(f.fAnimAngle).keys) do keys[#keys + 1] = { frame, v } end
				table.sort(keys, function(p, q) return p[1] < q[1] end)
				out.bones[i] = {
					name = S(b).name, parent = f.fParent, x = f.fPos.x, y = f.fPos.y,
					angle = f.fAngle, length = f.fLength, angle_keys = keys,
					parent_key = S(f.fAnimParent).keys[0],
				}
			end
		end
		return out
	end
	local layers = {}
	for i, st in ipairs(env.doc.layers) do layers[i] = layer_snapshot(st) end
	return {
		layers = layers,
		start_frame = env.doc.start_frame,
		end_frame = env.doc.end_frame,
		dirty = env.doc.dirty,
		frame = env.frame,
		alerts = env.alerts,
		selected = env.selected and env.selected.name or nil,
		ui_updates = env.ui_updates,
	}
end

return M
