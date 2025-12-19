import mineflayer from "mineflayer";
import pathfinderPkg from "mineflayer-pathfinder";
import collectBlockPkg from "mineflayer-collectblock";
import viewerPkg from "prismarine-viewer";
import { WebSocketServer } from "ws";
import { Vec3 } from "vec3";

/**
 * Robustly resolve a mineflayer plugin function from an imported module.
 * Works across: CommonJS default import wrappers, named exports, and nested defaults.
 */
function resolvePlugin(mod, candidates = []) {
  const seen = new Set();
  const queue = [mod];

  while (queue.length) {
    const cur = queue.shift();
    if (!cur || seen.has(cur)) continue;
    seen.add(cur);

    // direct function export
    if (typeof cur === "function") return cur;

    // check candidate properties first (common plugin patterns)
    for (const key of candidates) {
      if (cur && typeof cur[key] === "function") return cur[key];
    }

    // common places where CJS ends up inside ESM wrappers
    if (cur.default && !seen.has(cur.default)) queue.push(cur.default);

    // also walk 1 level of object values (safe enough here)
    if (typeof cur === "object") {
      for (const v of Object.values(cur)) {
        if (v && !seen.has(v)) queue.push(v);
      }
    }
  }

  return null;
}

// ---- Resolve mineflayer-pathfinder exports ----
const pathfinder =
  pathfinderPkg?.pathfinder ??
  pathfinderPkg?.default?.pathfinder ??
  (typeof pathfinderPkg === "function" ? pathfinderPkg : null);

const Movements = pathfinderPkg?.Movements ?? pathfinderPkg?.default?.Movements;
const goals = pathfinderPkg?.goals ?? pathfinderPkg?.default?.goals;

// ---- Resolve mineflayer-collectblock plugin function (THIS FIXES YOUR ASSERTION) ----
const collectBlockPlugin = resolvePlugin(collectBlockPkg, ["collectBlock"]);

// ---- Resolve prismarine-viewer mineflayer hook ----
// Docs/readme commonly expose { mineflayer } (CJS). :contentReference[oaicite:2]{index=2}
const mineflayerViewer =
  viewerPkg?.mineflayer ??
  viewerPkg?.default?.mineflayer ??
  viewerPkg?.viewer ?? // fallback for older shapes
  viewerPkg?.default?.viewer;

// ---- Basic sanity checks (fail fast with a helpful message) ----
if (typeof pathfinder !== "function") {
  throw new Error(
    `mineflayer-pathfinder export not resolved to a function. Got: ${typeof pathfinder}`
  );
}
if (typeof collectBlockPlugin !== "function") {
  throw new Error(
    `mineflayer-collectblock export not resolved to a function. Got: ${typeof collectBlockPlugin}`
  );
}
if (typeof mineflayerViewer !== "function") {
  console.warn(
    "[warn] prismarine-viewer mineflayer hook not found as a function. Viewer will be disabled."
  );
}

// ---- Config ----
const MC_HOST = process.env.MC_HOST ?? "127.0.0.1";
const MC_PORT = Number(process.env.MC_PORT ?? 25565);
const MC_USERNAME = process.env.MC_USERNAME ?? "PyBot";
const MC_VERSION = process.env.MC_VERSION ?? false; // auto-detect if false

const WS_PORT = Number(process.env.WS_PORT ?? 8765);
const VIEWER_PORT = Number(process.env.VIEWER_PORT ?? 3007);

// ---- Create bot ----
const bot = mineflayer.createBot({
  host: MC_HOST,
  port: MC_PORT,
  username: MC_USERNAME,
  version: MC_VERSION
});

// Load plugins (must be functions)
bot.loadPlugin(pathfinder);
bot.loadPlugin(collectBlockPlugin);

// ---- Web viewer (watch the bot) ----
bot.once("spawn", () => {
  if (typeof mineflayerViewer === "function") {
    mineflayerViewer(bot, { port: VIEWER_PORT, firstPerson: true });
    console.log(`[viewer] http://localhost:${VIEWER_PORT}`);
  } else {
    console.log("[viewer] disabled (viewer hook not available)");
  }
});

// ---- Helpers ----
function ok(ws, data = {}) {
  ws.send(JSON.stringify({ type: "ok", ...data }));
}
function err(ws, message, data = {}) {
  ws.send(JSON.stringify({ type: "error", message, ...data }));
}

function safeVec3(obj) {
  if (!obj) return null;
  const x = Number(obj.x),
    y = Number(obj.y),
    z = Number(obj.z);
  if ([x, y, z].some((n) => Number.isNaN(n))) return null;
  return new Vec3(x, y, z);
}

function serializeItem(item) {
  if (!item) return null;
  return {
    name: item.name,
    displayName: item.displayName,
    count: item.count,
    type: item.type,
    metadata: item.metadata,
    slot: item.slot
  };
}

async function equipByName(name, destination = "hand") {
  const item = bot.inventory.items().find((i) => i.name === name);
  if (!item) throw new Error(`Item not found in inventory: ${name}`);
  await bot.equip(item, destination);
  return item;
}

let mcDataCache = null;
async function getMcData() {
  if (!mcDataCache) {
    mcDataCache = await import("minecraft-data").then((m) => m.default(bot.version));
  }
  return mcDataCache;
}

function getInventoryCount(name) {
  return bot
    .inventory
    .items()
    .filter((i) => i.name === name)
    .reduce((sum, i) => sum + i.count, 0);
}

function getInventoryItemsMatching(predicate) {
  return bot.inventory.items().filter(predicate);
}

function getPlankItems() {
  return getInventoryItemsMatching((i) => i.name.endsWith("_planks"));
}

function getPlankCount() {
  return getPlankItems().reduce((sum, i) => sum + i.count, 0);
}

function getLogItems() {
  return getInventoryItemsMatching((i) => i.name.endsWith("_log") || i.name.endsWith("_stem"));
}

function logToPlankName(logName) {
  if (logName.endsWith("_log")) return logName.replace("_log", "_planks");
  if (logName.endsWith("_stem")) return logName.replace("_stem", "_planks");
  return "oak_planks";
}

async function craftItem(name, count = 1, tableBlock = null) {
  const mcData = await getMcData();
  const item = mcData.itemsByName?.[name];
  if (!item) throw new Error(`Unknown item: ${name}`);
  const recipes = bot.recipesFor(item.id, null, 1, tableBlock);
  if (!recipes.length) throw new Error(`No recipe available for ${name}`);
  const recipe = recipes[0];
  const perCraft = recipe?.result?.count ?? 1;
  const craftTimes = Math.ceil(count / perCraft);
  await bot.craft(recipe, craftTimes, tableBlock);
}

async function ensureCraftingTable() {
  const tableCount = getInventoryCount("crafting_table");
  if (!tableCount) {
    if (getPlankCount() < 4) {
      const logItem = getLogItems()[0];
      if (!logItem) throw new Error("No logs available to craft planks");
      await craftItem(logToPlankName(logItem.name), 4);
    }
    await craftItem("crafting_table", 1);
  }
  const reference = bot.blockAt(bot.entity.position.offset(0, -1, 0));
  if (!reference) throw new Error("No block to place crafting table on");
  await equipByName("crafting_table");
  await bot.placeBlock(reference, new Vec3(0, 1, 0));
  return bot.blockAt(reference.position.offset(0, 1, 0));
}

const pickaxeRank = new Map([
  ["wooden_pickaxe", 1],
  ["stone_pickaxe", 2],
  ["iron_pickaxe", 3],
  ["diamond_pickaxe", 4],
  ["netherite_pickaxe", 5]
]);

function getBestPickaxeName() {
  const pickaxes = bot.inventory.items().filter((i) => pickaxeRank.has(i.name));
  pickaxes.sort((a, b) => pickaxeRank.get(b.name) - pickaxeRank.get(a.name));
  return pickaxes[0]?.name ?? null;
}

function getLogBlockNames() {
  return [
    "oak_log",
    "spruce_log",
    "birch_log",
    "jungle_log",
    "acacia_log",
    "dark_oak_log",
    "mangrove_log",
    "cherry_log",
    "crimson_stem",
    "warped_stem"
  ];
}

async function ensureLogs(minCount = 1) {
  if (getLogItems().reduce((sum, i) => sum + i.count, 0) >= minCount) return;
  if (!bot.collectBlock?.collect) throw new Error("collectBlock plugin not available");
  const logBlocks = bot.findBlocks({
    matching: (b) => getLogBlockNames().includes(b?.name),
    maxDistance: 32,
    count: minCount
  });
  if (!logBlocks.length) throw new Error("No logs found nearby");
  const blocks = logBlocks.map((pos) => bot.blockAt(pos)).filter(Boolean);
  await bot.collectBlock.collect(blocks.slice(0, minCount));
}

async function ensurePlanks(minCount = 4) {
  if (getPlankCount() >= minCount) return;
  await ensureLogs(1);
  const logItem = getLogItems()[0];
  if (!logItem) throw new Error("No logs available to craft planks");
  await craftItem(logToPlankName(logItem.name), Math.max(4, minCount));
}

async function ensureSticks(minCount = 2) {
  if (getInventoryCount("stick") >= minCount) return;
  await ensurePlanks(2);
  await craftItem("stick", minCount);
}

async function ensureCraftingTablePlaced() {
  const tableBlock = await ensureCraftingTable();
  if (!tableBlock) throw new Error("Unable to place crafting table");
  return tableBlock;
}

async function ensurePickaxeTier(targetName) {
  const targetRank = pickaxeRank.get(targetName);
  if (!targetRank) throw new Error(`Unknown pickaxe tier: ${targetName}`);
  const best = getBestPickaxeName();
  if (best && pickaxeRank.get(best) >= targetRank) return best;

  const tableBlock = await ensureCraftingTablePlaced();

  if (targetName === "wooden_pickaxe") {
    await ensurePlanks(3);
    await ensureSticks(2);
    await craftItem("wooden_pickaxe", 1, tableBlock);
    return "wooden_pickaxe";
  }

  if (targetName === "stone_pickaxe") {
    await ensurePickaxeTier("wooden_pickaxe");
    await ensureItem("cobblestone", 3);
    await ensureSticks(2);
    await craftItem("stone_pickaxe", 1, tableBlock);
    return "stone_pickaxe";
  }

  if (targetName === "iron_pickaxe") {
    await ensurePickaxeTier("stone_pickaxe");
    await ensureItem("iron_ingot", 3);
    await ensureSticks(2);
    await craftItem("iron_pickaxe", 1, tableBlock);
    return "iron_pickaxe";
  }

  if (targetName === "diamond_pickaxe") {
    await ensurePickaxeTier("iron_pickaxe");
    await ensureItem("diamond", 3);
    await ensureSticks(2);
    await craftItem("diamond_pickaxe", 1, tableBlock);
    return "diamond_pickaxe";
  }

  throw new Error(`Unsupported pickaxe tier: ${targetName}`);
}

async function ensureToolByName(toolName) {
  if (toolName.endsWith("_pickaxe")) {
    return ensurePickaxeTier(toolName);
  }
  throw new Error(`Unsupported required tool: ${toolName}`);
}

async function ensureFuelItem() {
  const items = bot.inventory.items();
  const fuelItem =
    items.find((i) => i.name === "coal") ||
    items.find((i) => i.name === "charcoal") ||
    items.find((i) => i.name.endsWith("_planks")) ||
    items.find((i) => i.name.endsWith("_log") || i.name.endsWith("_stem")) ||
    items.find((i) => i.name === "stick");
  if (fuelItem) return fuelItem.name;
  await ensurePlanks(2);
  return getPlankItems()[0]?.name ?? "oak_planks";
}

async function ensureFurnacePlaced() {
  if (getInventoryCount("furnace") < 1) {
    await ensureItem("cobblestone", 8);
    const tableBlock = await ensureCraftingTablePlaced();
    await craftItem("furnace", 1, tableBlock);
  }
  const reference = bot.blockAt(bot.entity.position.offset(0, -1, 0));
  if (!reference) throw new Error("No block to place furnace on");
  await equipByName("furnace");
  await bot.placeBlock(reference, new Vec3(0, 1, 0));
  return bot.blockAt(reference.position.offset(0, 1, 0));
}

async function smeltItem(outputName, inputName, count = 1) {
  const furnaceBlock = await ensureFurnacePlaced();
  const furnace = await bot.openFurnace(furnaceBlock);
  try {
    const fuelName = await ensureFuelItem();
    const fuelItem = bot.inventory.items().find((i) => i.name === fuelName);
    const inputItem = bot.inventory.items().find((i) => i.name === inputName);
    if (!fuelItem) throw new Error(`Missing fuel item: ${fuelName}`);
    if (!inputItem) throw new Error(`Missing smelt input: ${inputName}`);
    await furnace.putFuel(fuelItem.type, null, 1);
    await furnace.putInput(inputItem.type, null, count);
    await furnace.takeOutput();
  } finally {
    furnace.close();
  }
  if (getInventoryCount(outputName) < count) {
    throw new Error(`Failed to smelt ${outputName}`);
  }
}

async function ensureItem(name, count = 1) {
  if (getInventoryCount(name) >= count) return;

  const mcData = await getMcData();

  async function collectBlockByName(blockName, blockCount) {
    if (!bot.collectBlock?.collect) throw new Error("collectBlock plugin not available");
    const matches = bot.findBlocks({
      matching: (b) => b?.name === blockName,
      maxDistance: 64,
      count: blockCount
    });
    if (!matches.length) throw new Error(`No blocks found: ${blockName}`);
    const sample = bot.blockAt(matches[0]);
    if (sample && !bot.canDigBlock(sample)) {
      const toolIds = Object.keys(sample.harvestTools ?? {}).map((id) => Number(id));
      const toolNames = toolIds
        .map((id) => mcData.items?.[id]?.name)
        .filter(Boolean);
      const pickaxeName = toolNames.find((tool) => tool.endsWith("_pickaxe"));
      if (pickaxeName) {
        await ensureToolByName(pickaxeName);
      } else if (!toolNames.length) {
        throw new Error(`Cannot dig ${blockName} with current tools`);
      }
    }
    const blocks = matches.map((pos) => bot.blockAt(pos)).filter(Boolean);
    await bot.collectBlock.collect(blocks.slice(0, blockCount));
  }

  if (name.endsWith("_planks")) {
    await ensurePlanks(count);
    return;
  }
  if (name === "stick") {
    await ensureSticks(count);
    return;
  }
  if (name.endsWith("_pickaxe")) {
    await ensurePickaxeTier(name);
    return;
  }

  if (mcData.blocksByName?.[name]) {
    await collectBlockByName(name, count);
    return;
  }

  const oreBlockName =
    mcData.blocksByName?.[`${name}_ore`]
      ? `${name}_ore`
      : name.startsWith("raw_") && mcData.blocksByName?.[`${name.replace("raw_", "")}_ore`]
        ? `${name.replace("raw_", "")}_ore`
        : null;
  if (oreBlockName) {
    await collectBlockByName(oreBlockName, count);
    if (getInventoryCount(name) >= count) return;
    throw new Error(`Collected ${oreBlockName} but still missing ${name}`);
  }

  const smeltables = {
    iron_ingot: ["raw_iron", "iron_ore"],
    gold_ingot: ["raw_gold", "gold_ore"],
    copper_ingot: ["raw_copper", "copper_ore"],
    stone: ["cobblestone"],
    glass: ["sand"],
    charcoal: ["oak_log"]
  };
  if (smeltables[name]) {
    const inputs = smeltables[name];
    for (const input of inputs) {
      if (getInventoryCount(input) >= count || mcData.blocksByName?.[input]) {
        await ensureItem(input, count);
        await smeltItem(name, input, count);
        return;
      }
    }
  }

  const item = mcData.itemsByName?.[name];
  if (!item) throw new Error(`Unknown item: ${name}`);
  const recipes = bot.recipesFor(item.id, null, 1, null);
  if (!recipes.length) throw new Error(`No recipe available for ${name}`);
  const recipe = recipes[0];
  const ingredients = recipe.ingredients ?? [];
  const ingredientCounts = ingredients.reduce((acc, id) => {
    if (!id) return acc;
    acc.set(id, (acc.get(id) ?? 0) + 1);
    return acc;
  }, new Map());

  for (const [id, needed] of ingredientCounts.entries()) {
    const ingredientName = mcData.items?.[id]?.name;
    if (!ingredientName) continue;
    await ensureItem(ingredientName, needed);
  }

  const tableBlock = recipe.requiresTable ? await ensureCraftingTablePlaced() : null;
  await craftItem(name, count, tableBlock);
}

// ---- Movement control (simple WASD-like) ----
const controlStates = {
  forward: false,
  back: false,
  left: false,
  right: false,
  jump: false,
  sprint: false,
  sneak: false
};

function applyControlStates() {
  for (const [k, v] of Object.entries(controlStates)) {
    bot.setControlState(k, !!v);
  }
}

let basePosition = null;
let autoActionsEnabled = true;
let autoActionBusy = false;

async function maybeEat() {
  if (bot.food >= 18) return false;
  const mcData = await getMcData();
  const foodItem = bot
    .inventory
    .items()
    .find((i) => mcData.foodsByName?.[i.name]);
  if (!foodItem) return false;
  await bot.equip(foodItem, "hand");
  await bot.consume();
  return true;
}

function findHostileEntity() {
  return bot.nearestEntity((entity) => entity.type === "mob" && entity.kind === "Hostile");
}

async function maybeDefend() {
  const target = findHostileEntity();
  if (!target) return false;
  if (bot.entity.position.distanceTo(target.position) > 6) return false;
  await bot.attack(target);
  return true;
}

setInterval(async () => {
  if (!autoActionsEnabled || autoActionBusy) return;
  autoActionBusy = true;
  try {
    await maybeEat();
    await maybeDefend();
  } catch (e) {
    console.warn("[auto-actions] error:", e?.message ?? e);
  } finally {
    autoActionBusy = false;
  }
}, 1000);

// ---- WebSocket control server ----
const wss = new WebSocketServer({ port: WS_PORT });
console.log(`[ws] listening on ws://localhost:${WS_PORT}`);

wss.on("connection", (ws) => {
  ok(ws, { message: "connected" });

  ws.on("message", async (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString("utf8"));
    } catch {
      return err(ws, "Invalid JSON");
    }

    const cmd = msg.cmd;
    const id = msg.id ?? null;

    async function replyOk(data = {}) {
      ok(ws, { id, ...data });
    }
    async function replyErr(message, data = {}) {
      err(ws, message, { id, ...data });
    }

    try {
      if (!cmd) return replyErr("Missing cmd");

      if (cmd === "status") {
        return replyOk({
          type: "status",
          username: bot.username,
          health: bot.health,
          food: bot.food,
          pos: bot.entity?.position ? bot.entity.position : null,
          yaw: bot.entity?.yaw ?? null,
          pitch: bot.entity?.pitch ?? null
        });
      }

      if (cmd === "chat") {
        bot.chat(String(msg.text ?? ""));
        return replyOk({ type: "chat" });
      }

      if (cmd === "control") {
        const state = msg.state ?? {};
        for (const k of Object.keys(controlStates)) {
          if (k in state) controlStates[k] = !!state[k];
        }
        applyControlStates();
        return replyOk({ type: "control", state: controlStates });
      }

      if (cmd === "look_at") {
        const v = safeVec3(msg.pos);
        if (!v) return replyErr("Invalid pos");
        await bot.lookAt(v, true);
        return replyOk({ type: "look_at" });
      }

      if (cmd === "goto") {
        const v = safeVec3(msg.pos);
        if (!v) return replyErr("Invalid pos");
        const range = Number(msg.range ?? 1);

        if (!Movements || !goals) {
          return replyErr(
            "Pathfinder helpers not available (Movements/goals). Check mineflayer-pathfinder version."
          );
        }

        const mcData = await import("minecraft-data").then((m) => m.default(bot.version));
        const defaultMove = new Movements(bot, mcData);
        bot.pathfinder.setMovements(defaultMove);

        const goal = new goals.GoalNear(v.x, v.y, v.z, range);
        bot.pathfinder.setGoal(goal, false);

        return replyOk({ type: "goto", target: { x: v.x, y: v.y, z: v.z }, range });
      }

      if (cmd === "stop") {
        bot.pathfinder.setGoal(null);
        for (const k of Object.keys(controlStates)) controlStates[k] = false;
        applyControlStates();
        return replyOk({ type: "stop" });
      }

      if (cmd === "set_base") {
        const pos = bot.entity?.position;
        if (!pos) return replyErr("Bot position unavailable");
        basePosition = pos.clone();
        return replyOk({
          type: "set_base",
          base: { x: basePosition.x, y: basePosition.y, z: basePosition.z }
        });
      }

      if (cmd === "get_base") {
        if (!basePosition) return replyErr("Base position not set");
        return replyOk({
          type: "get_base",
          base: { x: basePosition.x, y: basePosition.y, z: basePosition.z }
        });
      }

      if (cmd === "get") {
        const name = String(msg.name ?? "");
        const count = Number(msg.count ?? 1);
        if (!name) return replyErr("Missing item name");
        if (!Number.isFinite(count) || count <= 0) return replyErr("Invalid count");
        await ensureItem(name, count);
        return replyOk({ type: "get", name, count, status: "completed" });
      }

      if (cmd === "automatic_actions") {
        autoActionsEnabled = !!msg.enabled;
        return replyOk({ type: "automatic_actions", enabled: autoActionsEnabled });
      }

      if (cmd === "dig") {
        const v = safeVec3(msg.pos);
        if (!v) return replyErr("Invalid pos");
        const block = bot.blockAt(v);
        if (!block) return replyErr("No block at pos");
        await bot.dig(block);
        return replyOk({ type: "dig", block: block.name });
      }

      if (cmd === "use") {
        const v = safeVec3(msg.pos);
        if (!v) return replyErr("Invalid pos");
        const block = bot.blockAt(v);
        if (!block) return replyErr("No block at pos");
        await bot.activateBlock(block);
        return replyOk({ type: "use", block: block.name });
      }

      if (cmd === "collect") {
        const name = String(msg.name ?? "");
        const count = Number(msg.count ?? 1);
        const radius = Number(msg.radius ?? 32);

        if (!name) return replyErr("Missing block name");
        if (!Number.isFinite(count) || count <= 0) return replyErr("Invalid count");
        if (!bot.collectBlock?.collect) return replyErr("collectBlock plugin not available on bot");

        const matches = bot.findBlocks({
          matching: (b) => b?.name === name,
          maxDistance: radius,
          count: count
        });

        if (!matches.length) return replyErr(`No blocks found: ${name} (radius ${radius})`);

        const blocks = matches.map((p) => bot.blockAt(p)).filter(Boolean);
        await bot.collectBlock.collect(blocks.slice(0, count));
        return replyOk({ type: "collect", name, collected: Math.min(count, blocks.length) });
      }

      if (cmd === "place") {
        const ref = safeVec3(msg.reference);
        if (!ref) return replyErr("Invalid reference");
        const face = Number(msg.face ?? 1);
        const itemName = String(msg.itemName ?? "");
        if (!itemName) return replyErr("Missing itemName");

        const referenceBlock = bot.blockAt(ref);
        if (!referenceBlock) return replyErr("No reference block at reference");

        await equipByName(itemName, "hand");

        const faces = [
          new Vec3(0, -1, 0),
          new Vec3(0, 1, 0),
          new Vec3(0, 0, -1),
          new Vec3(0, 0, 1),
          new Vec3(-1, 0, 0),
          new Vec3(1, 0, 0)
        ];
        const dir = faces[face] ?? faces[1];
        await bot.placeBlock(referenceBlock, dir);

        return replyOk({ type: "place", itemName });
      }

      if (cmd === "inventory") {
        const items = bot.inventory.items().map(serializeItem);
        return replyOk({ type: "inventory", items });
      }

      if (cmd === "scan_blocks") {
        const radius = Number(msg.radius ?? 6);
        if (!Number.isFinite(radius) || radius <= 0) return replyErr("Invalid radius");
        const blocks = bot.findBlocks({
          matching: () => true,
          maxDistance: radius,
          count: 100
        });
        const results = blocks
          .map((pos) => bot.blockAt(pos))
          .filter(Boolean)
          .map((block) => ({
            name: block.name,
            position: { x: block.position.x, y: block.position.y, z: block.position.z }
          }));
        return replyOk({ type: "scan_blocks", radius, blocks: results });
      }

      if (cmd === "find_blocks") {
        const name = String(msg.name ?? "");
        const radius = Number(msg.radius ?? 16);
        const count = Number(msg.count ?? 10);
        if (!name) return replyErr("Missing block name");
        if (!Number.isFinite(radius) || radius <= 0) return replyErr("Invalid radius");
        if (!Number.isFinite(count) || count <= 0) return replyErr("Invalid count");
        const matches = bot.findBlocks({
          matching: (b) => b?.name === name,
          maxDistance: radius,
          count
        });
        const results = matches
          .map((pos) => bot.blockAt(pos))
          .filter(Boolean)
          .map((block) => ({
            name: block.name,
            position: { x: block.position.x, y: block.position.y, z: block.position.z }
          }));
        return replyOk({ type: "find_blocks", name, radius, blocks: results });
      }

      if (cmd === "toss") {
        const name = String(msg.name ?? "");
        const count = Number(msg.count ?? 1);
        if (!name) return replyErr("Missing item name");
        const item = bot.inventory.items().find((i) => i.name === name);
        if (!item) return replyErr(`Item not found: ${name}`);
        await bot.toss(item.type, null, Math.min(count, item.count));
        return replyOk({ type: "toss", name, count: Math.min(count, item.count) });
      }

      return replyErr(`Unknown cmd: ${cmd}`);
    } catch (e) {
      return replyErr(String(e?.message ?? e));
    }
  });
});

// ---- Emit events to all WS clients ----
function broadcast(obj) {
  const data = JSON.stringify(obj);
  for (const client of wss.clients) {
    if (client.readyState === 1) client.send(data);
  }
}

bot.on("chat", (username, message) => {
  broadcast({ type: "event", event: "chat", username, message });
});

bot.on("health", () => {
  broadcast({ type: "event", event: "health", health: bot.health, food: bot.food });
});

bot.on("move", () => {
  const p = bot.entity?.position;
  if (!p) return;
  broadcast({ type: "event", event: "pos", pos: { x: p.x, y: p.y, z: p.z } });
});

bot.on("kicked", (reason) => console.log("[bot] kicked:", reason));
bot.on("error", (e) => console.log("[bot] error:", e));
