"""Map Overlay agent pass — one data-oriented iteration shared by both modes.

Walks the agent arrays once, reads each agent's struct a single time (Mission Map's efficient
``GetAgentByID`` → ``GetAsAgentLiving`` pattern), classifies it against the model tables, and
draws through the supplied :class:`~.projection.Projection` + shape registry. This is the
single copy of the classification logic that both widgets used to carry independently — spirit
detection, boss glow, item rarity, npc/minipet/merchant, gadget/chest, pet, custom markers.

The pass is projection-agnostic: it asks the projection to place points and size ring radii,
and passes ``projection.rotation`` to the shapes as the rigid glyph rotation. In compass mode
it additionally distance-culls agents (Compass behaviour); the mission map shows everything.
"""

from typing import Optional

from Core.Agent import Agent
from Core.AgentArray import AgentArray
from Core.Item import Item
from Core.Player import Player
from Core.native_src.context.AgentContext import AgentArray as AgentArrayContext

from . import shapes
from .model import CHEST_GADGET_IDS
from .model import ITEM_RARITY_COLORS
from .model import MARKER_ALLY_NPC
from .model import MARKER_CHEST
from .model import MARKER_ENEMY
from .model import MARKER_ENEMY_PET
from .model import MARKER_GADGET
from .model import MARKER_ITEM
from .model import MARKER_MERCHANT
from .model import MARKER_MINION
from .model import MARKER_MINIPET
from .model import MARKER_NEUTRAL
from .model import MARKER_NPC
from .model import MARKER_PET
from .model import MARKER_PLAYER
from .model import MARKER_PLAYERS
from .model import PET_MODEL_IDS
from .model import PROFESSION_COLORS
from .model import MarkerStyle
from .model import OverlayConfig
from .model import OverlayMode
from .model import RGBA
from .model import classify_spirit
from .projection import Projection

TARGET_ACCENT: RGBA = (235, 235, 50, 255)
TARGET_SIZE_BONUS = 2.0


class AgentPass:
    def __init__(self, cfg: OverlayConfig) -> None:
        self.cfg = cfg
        # Click-target hit boxes, refilled each pass. The Interaction shares this exact list
        # object (a plain list.append per marker beats a callback frame per marker).
        self.hits: list[tuple[int, float, float, float]] = []
        # Screen rect (padded) used to skip markers projected outside the frame.
        self._clip: Optional[tuple[float, float, float, float]] = None
        # Merchant-ness never changes within a map; name decoding is expensive, so memoize it.
        self._merchant_cache: dict[int, bool] = {}

    def reset_for_map_change(self) -> None:
        self._merchant_cache.clear()

    def _is_merchant(self, agent_id: int) -> bool:
        cached = self._merchant_cache.get(agent_id)
        if cached is None:
            cached = "MERCHANT" in Agent.GetNameByID(agent_id).upper()
            self._merchant_cache[agent_id] = cached
        return cached

    # ── low-level draw ───────────────────────────────────────────────────────────────────
    def _marker(
        self,
        proj: Projection,
        style: MarkerStyle,
        gx: float,
        gy: float,
        facing: float,
        agent_id: int,
        target_id: int,
        color_override: Optional[RGBA] = None,
        shape_override: Optional[str] = None,
        size_bonus: float = 0.0,
        accent_override: Optional[RGBA] = None,
    ) -> None:
        if not style.visible:
            return
        sx, sy = proj.game_to_screen(gx, gy)
        clip = self._clip
        if clip is not None and (sx < clip[0] or sx > clip[2] or sy < clip[1] or sy > clip[3]):
            return  # off-frame: skip the glyph and its hit box entirely
        if agent_id == target_id:
            accent = TARGET_ACCENT
            size_bonus += TARGET_SIZE_BONUS
        elif accent_override is not None:
            accent = accent_override
        else:
            accent = style.accent
        color = color_override if color_override is not None else style.color
        shape = shape_override if shape_override is not None else style.shape
        total_size = style.size + size_bonus
        shapes.draw_marker(shape, sx, sy, total_size, color, accent, facing, proj.rotation)
        if agent_id:
            self.hits.append((agent_id, sx, sy, total_size))

    def _spirit(
        self,
        proj: Projection,
        gx: float,
        gy: float,
        facing: float,
        agent_id: int,
        target_id: int,
        marker_key: str,
        range_value: int,
        aura_shift_to: Optional[RGBA] = None,
    ) -> None:
        """Draw a spirit: its own marker colour, plus its range aura.

        ``aura_shift_to`` tints only the *aura* toward another colour (Mission Map blends a
        hostile spirit's aura 55% toward enemy red while the marker keeps the spirit's colour,
        so the spirit stays identifiable).
        """
        style = self.cfg.markers.get(marker_key)
        if style is None:
            return
        sx, sy = proj.game_to_screen(gx, gy)
        if self.cfg.show_spirit_range and range_value > 0:
            radius = proj.gwinch_to_pixels(range_value)
            base = style.color if aura_shift_to is None else shapes.shift_rgba(style.color, aura_shift_to, 0.55)
            aura: RGBA = (base[0], base[1], base[2], int(self.cfg.spirit_alpha))
            shapes.draw_aura(sx, sy, radius, aura, shapes.segments_for_radius(radius))
        self._marker(proj, style, gx, gy, facing, agent_id, target_id)

    # ── helpers ──────────────────────────────────────────────────────────────────────────
    def _custom_marker(self, proj, obj, living, agent_id, target_id) -> bool:
        """Draw the agent via a matching user custom marker (by model id). True if handled."""
        model_id = int(living.player_number)
        for cm in self.cfg.custom_markers.values():
            if cm.visible and model_id == cm.model_id:
                style = MarkerStyle(cm.name, True, cm.shape, cm.color, (0, 0, 0, 255), float(cm.size))
                self._marker(proj, style, obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id)
                return True
        return False

    # NOTE: the alive test is inlined at each call site rather than factored into a helper —
    # it runs once per agent per frame and the method-call overhead showed up in profiling.

    # ── main pass ────────────────────────────────────────────────────────────────────────
    def draw(self, proj: Projection) -> None:
        cfg = self.cfg
        player_id = Player.GetAgentID()
        target_id = Player.GetTargetID()

        cull = cfg.mode is OverlayMode.COMPASS
        cull_r2 = float(cfg.position.culling) ** 2
        px, py = proj.player_pos
        # Short-circuits below keep these out of the per-agent path when they do not apply:
        # with no custom markers configured the lookup is skipped entirely, and the range test
        # is only called in compass mode (the mission map never culls).
        has_custom = bool(cfg.custom_markers)
        self.hits.clear()

        left, top, right, bottom = proj.content_rect()
        margin = 40.0
        self._clip = (left - margin, top - margin, right + margin, bottom + margin)

        def in_range(gx: float, gy: float) -> bool:
            dx = gx - px
            dy = gy - py
            return (dx * dx + dy * dy) <= cull_r2

        # Straight to the context accessor: Agent.GetAgentByID -> AgentArray.GetAgentByID each
        # perform a function-level import per call, which dominated the profile.
        get_agent = AgentArrayContext.GetAgentByID

        def living_of(agent_id: int):
            obj = get_agent(agent_id)
            if not obj:
                return None, None
            return obj, obj.GetAsAgentLiving()

        # Neutral
        for agent_id in AgentArray.GetNeutralArray():
            obj, living = living_of(agent_id)
            if (
                obj is None
                or living is None
                or not (living.hp > 0.0 and not (living.is_dead or living.is_dead_by_type_map))
                or (cull and not in_range(obj.pos.x, obj.pos.y))
            ):
                continue
            if has_custom and self._custom_marker(proj, obj, living, agent_id, target_id):
                continue
            self._marker(
                proj, cfg.markers[MARKER_NEUTRAL], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id
            )

        # Minions
        for agent_id in AgentArray.GetMinionArray():
            obj, living = living_of(agent_id)
            if (
                obj is None
                or living is None
                or not (living.hp > 0.0 and not (living.is_dead or living.is_dead_by_type_map))
                or (cull and not in_range(obj.pos.x, obj.pos.y))
            ):
                continue
            if has_custom and self._custom_marker(proj, obj, living, agent_id, target_id):
                continue
            self._marker(
                proj, cfg.markers[MARKER_MINION], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id
            )

        # Spirits / pets
        for agent_id in AgentArray.GetSpiritPetArray():
            obj, living = living_of(agent_id)
            if obj is None or living is None or (cull and not in_range(obj.pos.x, obj.pos.y)):
                continue
            if living.is_spawned and not (living.hp > 0.0 and not (living.is_dead or living.is_dead_by_type_map)):
                continue
            if has_custom and self._custom_marker(proj, obj, living, agent_id, target_id):
                continue
            if not living.is_spawned:
                self._marker(
                    proj, cfg.markers[MARKER_PET], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id
                )
                continue
            info = classify_spirit(int(living.player_number))
            if info is None:
                self._marker(
                    proj, cfg.markers[MARKER_NEUTRAL], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id
                )
            else:
                self._spirit(
                    proj,
                    obj.pos.x,
                    obj.pos.y,
                    obj.rotation_angle,
                    agent_id,
                    target_id,
                    info.marker_key,
                    info.range_value,
                )

        # Enemies
        for agent_id in AgentArray.GetEnemyArray():
            obj, living = living_of(agent_id)
            if (
                obj is None
                or living is None
                or not (living.hp > 0.0 and not (living.is_dead or living.is_dead_by_type_map))
                or (cull and not in_range(obj.pos.x, obj.pos.y))
            ):
                continue
            if has_custom and self._custom_marker(proj, obj, living, agent_id, target_id):
                continue
            enemy_style = cfg.markers[MARKER_ENEMY]
            model_id = int(living.player_number)
            # Struct fields, not Agent.HasBossGlow / Agent.GetProfessionIDs — both of those
            # refetch the whole living agent to read a single field we already hold.
            if living.has_boss_glow:
                # Profession 0 is "None" (grey in the table) and plenty of bosses report it —
                # selecting it would make a boss read *duller* than a normal enemy, so only
                # real professions (1..10) recolour; otherwise keep the enemy colour. The boss
                # accent + size bump keep bosses distinct either way.
                color = enemy_style.color
                if cfg.boss_profession_colors:
                    prof = int(living.primary)
                    if 1 <= prof < len(PROFESSION_COLORS):
                        color = PROFESSION_COLORS[prof]
                self._marker(
                    proj,
                    enemy_style,
                    obj.pos.x,
                    obj.pos.y,
                    obj.rotation_angle,
                    agent_id,
                    target_id,
                    color_override=color,
                    size_bonus=enemy_style.size * 0.2,
                    accent_override=cfg.boss_accent,
                )
                continue
            if not living.is_spawned:
                key = MARKER_ENEMY_PET if model_id in PET_MODEL_IDS else MARKER_ENEMY
                self._marker(proj, cfg.markers[key], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id)
                continue
            info = classify_spirit(model_id)
            if info is None:
                self._marker(proj, enemy_style, obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id)
            else:
                self._spirit(
                    proj,
                    obj.pos.x,
                    obj.pos.y,
                    obj.rotation_angle,
                    agent_id,
                    target_id,
                    info.marker_key,
                    info.range_value,
                    aura_shift_to=enemy_style.color,
                )

        # Allies (party players + allied NPCs)
        for agent_id in AgentArray.GetAllyArray():
            obj, living = living_of(agent_id)
            if (
                obj is None
                or living is None
                or not (living.hp > 0.0 and not (living.is_dead or living.is_dead_by_type_map))
                or (cull and not in_range(obj.pos.x, obj.pos.y))
            ):
                continue
            if agent_id == player_id:
                continue
            if has_custom and self._custom_marker(proj, obj, living, agent_id, target_id):
                continue
            key = MARKER_ALLY_NPC if living.is_npc else MARKER_PLAYERS
            self._marker(proj, cfg.markers[key], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id)

        # Player
        if Player.IsPlayerLoaded():
            obj, living = living_of(player_id)
            if obj is not None and living is not None and (not cull or in_range(obj.pos.x, obj.pos.y)):
                self._marker(
                    proj, cfg.markers[MARKER_PLAYER], obj.pos.x, obj.pos.y, obj.rotation_angle, player_id, target_id
                )

        # NPCs / minipets / merchants
        for agent_id in AgentArray.GetNPCMinipetArray():
            obj, living = living_of(agent_id)
            if (
                obj is None
                or living is None
                or not (living.hp > 0.0 and not (living.is_dead or living.is_dead_by_type_map))
                or (cull and not in_range(obj.pos.x, obj.pos.y))
            ):
                continue
            if has_custom and self._custom_marker(proj, obj, living, agent_id, target_id):
                continue
            if int(living.level) > 1:
                if living.has_quest:  # struct field; Agent.HasQuest would refetch the agent
                    self._marker(
                        proj,
                        cfg.markers[MARKER_NPC],
                        obj.pos.x,
                        obj.pos.y,
                        obj.rotation_angle,
                        agent_id,
                        target_id,
                        shape_override="Star",
                    )
                elif self._is_merchant(agent_id):
                    self._marker(
                        proj,
                        cfg.markers[MARKER_MERCHANT],
                        obj.pos.x,
                        obj.pos.y,
                        obj.rotation_angle,
                        agent_id,
                        target_id,
                    )
                else:
                    self._marker(
                        proj, cfg.markers[MARKER_NPC], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id
                    )
            else:
                self._marker(
                    proj, cfg.markers[MARKER_MINIPET], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id
                )

        # Gadgets / chests
        for agent_id in AgentArray.GetGadgetArray():
            obj = get_agent(agent_id)
            if not obj:
                continue
            gadget = obj.GetAsAgentGadget()
            if not gadget or (cull and not in_range(obj.pos.x, obj.pos.y)):
                continue
            key = MARKER_CHEST if int(gadget.gadget_id) in CHEST_GADGET_IDS else MARKER_GADGET
            self._marker(proj, cfg.markers[key], obj.pos.x, obj.pos.y, obj.rotation_angle, agent_id, target_id)

        # Items (rarity-coloured)
        for agent_id in AgentArray.GetItemArray():
            obj = get_agent(agent_id)
            if not obj:
                continue
            item_agent = obj.GetAsAgentItem()
            if not item_agent or (cull and not in_range(obj.pos.x, obj.pos.y)):
                continue
            try:
                rarity = int(Item.item_instance(item_agent.item_id).rarity.value)
            except Exception:
                rarity = 0
            color = ITEM_RARITY_COLORS.get(rarity, ITEM_RARITY_COLORS[0])
            self._marker(
                proj,
                cfg.markers[MARKER_ITEM],
                obj.pos.x,
                obj.pos.y,
                obj.rotation_angle,
                agent_id,
                target_id,
                color_override=color,
            )
