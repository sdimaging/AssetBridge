"""Smart tags: one consistent, filterable vocabulary for every asset, whatever library it came from.

smart_tags(asset) -> {facet: sorted tags}. Facets: kind, material, setting, condition, size.
Derived from the asset name, folder names, vendor categories / tags (Megascans semantics), its type,
a botanical genus table (Maxtree / Megascans plants) and its real-world size. The vocabulary is small on
purpose: every tag is a word someone would click in a filter. TAGS_VERSION bumps re-tag the whole catalog.
"""
import re

TAGS_VERSION = 2
FACETS = ("kind", "material", "setting", "condition", "size")

# ---------------------------------------------------------------- plants by genus
_GENERA = {
    "palm": """Acanthophoenix Acoelorrhaphe Acrocomia Adonidia Alexandra Archontophoenix Areca Asterogyne Attalea
        Beccariophoenix Bentinckia Bismarckia Borassus Brahea Butia Carpentaria Caryota Chamaedorea Chamaerops
        Chuniophoenix Coccothrinax Cocos Cryosophila Cyrtostachys Dypsis Hyophorbe Laccospadix Licuala Phoenix
        Pritchardia Pseudophoenix Ravenea Rhapis Roystonea Sabal Saribus Syagrus Thrinax Trachycarpus Washingtonia
        Livistona Howea Zamia Cycas""",
    "conifer": """Abies Araucaria Cedrus Chamaecyparis Cryptomeria Larix Metasequoia Picea Pinus Podocarpus
        Sciadopitys Taxodium Juniperus Thuja Cupressus Taxus Sequoia Tsuga Pine Fir Spruce Cedar""",
    "tree": """Acer Adansonia Aesculus Aglaia Albizia Alnus Amygdalus Betula Broussonetia Celtis Cercis Cinnamomum
        Citrus Erythrina Falcataria Ficus Firmiana Fraxinus Handroanthus Liquidambar Melia Olea Platanus Populus
        Quercus Salix Tilia Toona Ulmus Acacia Banksia Fagus Beech Prunus Malus Magnolia Jacaranda Eucalyptus
        Oak Maple Birch Willow Poplar Elm""",
    "shrub": """Abelia Abutilon Aronia Aucuba Ancuba Baeckea Bougainvillea Boxwood Buxus Brufmansia Brugmansia
        Camellia Cistus Codiaeum Cotoneaster Duranta Elaeagnus Fothergilla Hippophae Hydrangea Kerria Lavandula
        Perovskia Rosa Rose Rosmarinus Santolina Spiraea Tibouchina Dracaena Cordyline Hibiscus Ligustrum Berberis
        Bilberry Broom Shrub Xeric""",
    "desert": """Alhagi Calligonum Gymnocarpos Halogeton Halostachys Haloxylon Hexinia Kalidium Leptadenia Rhazya
        Salsola Zygophyllum Desert""",
    "grass": """Achnatherum Alopecurus Ammophila Calamagrostis Capillipedium Carex Cortaderia Cynodon Cyperus
        Dactyloctenium Digitaria Festuca Hakonechloa Helictotrichon Hordeum Imperata Juncus Koeleria Lagurus Lolium
        Miscanthus Muhly Nassella Panicum Pennisetum Setaria Stipa Zoysia Kikuyu Barley Wheat Marram Thatching
        Liriope Ophiopogon Oplismenus Equisetum Rush Grass""",
    "bamboo": "Bambusa Alphonse Phyllostachys Bamboo",
    "fern": "Adiantum Asplenium Nephrolepis Dicksonia Polystichum Fern",
    "succulent": """Aeonium Agave Aloe Bergeranthus Cotyledon Crassula Echeveria Euphorbia Haworthia Kalanchoe Orbea
        Portulacaria Sansevieria Sedum Sempervivum Senecio Yucca Beaucarnea Zamioculcas Opuntia Cactus""",
    "aquatic": """Echinodorus Eichhornia Lemna Limnobium Myriophyllum Nelumbo Nymphaea Pistia Salvinia Seaweed
        Zantedeschia Arrowhead""",
    "vine": """Campsis Cissus Clematis Hedera Ipomoea Jasminum Lonicera Mandevilla Mucuna Parthenocissus
        Trachelospermum Vitis Allamanda Climbervines Creeper Tropaeolum Ivy""",
    "flower": """Aconitum Ageratum Allium Anemone Aruncus Aster Astrantia Bidens Catharanthus Convallaria Corydalis
        Dianthus Digitalis Erigeron Geranium Gladiolus Gnaphalium Goldenrods Hosta Iberis Iris Isotoma Ixeris
        Lythrum Narcissus Phlox Salvia Viola Agastache Alchemilla Amaranthus Androsace Lily Sigesbeckia Xanthium
        Lactuca Rumex Plantago Galium Capsella Mazus Lamium Tulip Daisy Sunflower Lavender""",
    "foliage": """Aglaonema Alocasia Alpinia Caladium Calathea Dieffenbachia Dizygotheca Heliconia Musa Philodendron
        Ravenala Strelitzia Ginger Taro Asparagus Arthropodium Curculigo Monstera Pandanus""",
    "herb": """Basil Coriander Foeniculum Melissa Lemonbalm Mentha Peppermint Oregano Origanum Perilla Petroselinum
        Rosemary Sage Thymus Chamomile Bay""",
    "groundcover": "Clover Clovers Ajuga Vinca Ground",
}
GENUS = {g.lower(): grp for grp, names in _GENERA.items() for g in names.split()}
# plant group -> (kind tags, setting tags)
PLANT_GROUP = {
    "palm": ({"plant", "palm", "tree"}, {"tropical"}),
    "conifer": ({"plant", "tree", "conifer"}, {"forest"}),
    "tree": ({"plant", "tree"}, set()),
    "shrub": ({"plant", "shrub"}, {"garden"}),
    "desert": ({"plant", "shrub"}, {"desert"}),
    "grass": ({"plant", "grass"}, set()),
    "bamboo": ({"plant", "bamboo"}, {"tropical"}),
    "fern": ({"plant", "fern"}, {"forest"}),
    "succulent": ({"plant", "succulent"}, {"desert"}),
    "aquatic": ({"plant", "aquatic"}, {"water"}),
    "vine": ({"plant", "vine"}, {"garden"}),
    "flower": ({"plant", "flower"}, {"garden"}),
    "foliage": ({"plant", "foliage"}, {"tropical", "interior"}),
    "herb": ({"plant", "herb"}, {"garden"}),
    "groundcover": ({"plant", "groundcover"}, {"garden"}),
}

# ---------------------------------------------------------------- keywords -> canonical tags
_WORDS = {
    "kind": {
        "tree": "tree trees trunk", "shrub": "shrub shrubs bush bushes hedge", "grass": "grass grasses lawn",
        "flower": "flower flowers blossom bloom", "plant": "plant plants vegetation foliage leaf leaves potted",
        "rock": "rock rocks boulder boulders pebble pebbles", "cliff": "cliff cliffs",
        "deadwood": "log logs stump stumps branch branches twig twigs deadwood driftwood",
        "debris": "debris rubble scatter pile",
        "furniture": "chair chairs table tables sofa couch bed shelf shelves cabinet desk bench stool wardrobe throne",
        "lighting": "lamp lamps lantern chandelier candle candles torch sconce",
        "container": "barrel barrels crate crates box boxes sack sacks bucket basket jar pot pots bottle bottles",
        "tools": "tool tools hammer hammers anvil axe saw tongs wrench",
        "architecture": "wall walls floor floors ceiling door doors window windows stairs stair pillar column beam "
                        "roof arch facade building house tower fence railing",
        "vehicle": "car cars bus truck ship ships boat drone drones bike vehicle aircraft plane cart",
        "weapon": "weapon weapons sword swords gun pistol cannon rifle shield spear",
        "bones": "skull skulls bone bones skeleton",
        "food": "food fruit fruits bread vegetable apple",
        "planter": "planter planters",
        "terrain": "terrain landscape hill dune embankment",
        "sky": "sky skies cloudy clouds",
        "kit": "kit kitbash",
    },
    "material": {
        "wood": "wood wooden plank planks timber bark parquet lumber",
        "metal": "metal metallic steel iron rust rusty copper brass bronze aluminium aluminum chrome tin",
        "stone": "stone stones granite cobble cobblestone slate sandstone limestone basalt",
        "marble": "marble", "concrete": "concrete cement", "brick": "brick bricks", "tile": "tile tiles tiled",
        "plaster": "plaster stucco", "fabric": "fabric cloth textile carpet rug linen canvas",
        "leather": "leather", "fur": "fur hair", "skin": "skin scales hide", "plastic": "plastic rubber vinyl",
        "glass": "glass", "paint": "paint painted", "asphalt": "asphalt road tarmac", "gravel": "gravel",
        "sand": "sand sandy dune", "soil": "soil dirt mud earth", "snow": "snow snowy", "ice": "ice icy frozen",
        "moss": "moss mossy lichen", "water": "water", "paper": "paper cardboard", "ceramic": "ceramic porcelain",
    },
    "setting": {
        "forest": "forest woods woodland jungle taiga", "tropical": "tropical jungle palm",
        "desert": "desert arid dune canyon", "winter": "winter snow snowy ice icy frozen tundra cold",
        "coastal": "beach coast coastal sea ocean shore", "urban": "urban city street sidewalk",
        "rural": "rural farm village countryside", "interior": "interior indoor room apartment office kitchen",
        "sci-fi": "scifi sci-fi futuristic space station spaceship cyberpunk", "medieval": "medieval castle forge throne",
        "industrial": "industrial factory warehouse", "garden": "garden park yard",
        "water": "underwater aquatic pond lake river", "fantasy": "fantasy magic", "pirate": "pirate pirates",
    },
    "condition": {
        "old": "old aged worn weathered ancient vintage", "damaged": "damaged broken cracked destroyed ruined",
        "dirty": "dirty grimy", "clean": "clean", "wet": "wet", "dry": "dry dried withered",
        "mossy": "mossy overgrown", "rusty": "rusty rusted", "burnt": "burnt burned charred",
    },
}
WORD = {}
for facet, groups in _WORDS.items():
    for tag, words in groups.items():
        for w in words.split():
            WORD.setdefault(w.replace("_3d", ""), []).append((facet, tag))

GENERIC_CONDITION = {"old", "clean", "dirty", "dry"}

TYPE_KIND = {"hdri": "hdri", "splat": "splat", "surface": "material", "decal": "decal", "atlas": "atlas",
             "plant": "plant", "scene": "scene"}

JUNK = re.compile(r"^(\d+|v\d+|lod\d+|var\d+|\d+k|[a-z]?\d+[a-z]?|.{1,2}|.*[\\/].*)$", re.I)
STOP = {"the", "and", "with", "for", "new", "folder", "source", "model", "models", "untitled", "copy", "final",
        "high", "low", "mid", "mesh", "asset", "assets", "file", "c4d", "fbx", "obj", "max", "blend", "octane",
        "corona", "vray", "pbr", "texture", "textures", "maps", "map", "preview", "version", "free", "sample"}


def clean_tags(tags):
    """Vendor / filename tags minus numbers, paths, file-format words and fragments."""
    out = []
    for t in tags:
        t = str(t).strip().lower()
        if not t or JUNK.match(t) or t in STOP or len(t) > 32:
            continue
        if t not in out:
            out.append(t)
    return out


def _tokens(*texts):
    toks = []
    for s in texts:
        for t in re.split(r"[^a-z0-9\-]+", str(s or "").lower()):
            if t:
                toks.append(t)
                if "-" in t:
                    toks.extend(t.split("-"))
    return toks


def _size(asset):
    m = asset.get("real_size_m")
    if not m:
        s = str(asset.get("size") or "")
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
        if nums and ("m" in s.lower() or "×" in s or "x" in s.lower()):
            m = max(nums)
            if "cm" in s.lower():
                m /= 100.0
    if not m:
        return None
    return "tiny" if m < 0.15 else "small" if m < 0.6 else "medium" if m < 2.5 else "large" if m < 8 else "huge"


def smart_tags(asset):
    tags = {f: set() for f in FACETS}
    typ = asset.get("type") or ""
    if typ in TYPE_KIND:
        tags["kind"].add(TYPE_KIND[typ])
    name = asset.get("name") or ""
    path = asset.get("path") or ""
    folders = " ".join(re.split(r"[\\/]+", path)[-4:])
    folders = re.sub(r"forest[_ ]?pack", " ", folders, flags=re.I)  # Maxtree's "Forest Pack" product, not a forest
    vendor = " ".join(list(asset.get("tags") or []) + list(asset.get("categories") or []))
    own = set(_tokens(name))
    parts = " ".join(list((asset.get("material_sets") or {}).keys()) + list(asset.get("parts") or []))
    parts = re.sub(r"(?i)\b(mt_pm_v\d+|mt)_", " ", parts)  # texture-set names: MT_PM_V50_Snow_01 -> snow
    for t in _tokens(name, folders, vendor, asset.get("subtitle"), parts):
        for facet, tag in WORD.get(t, ()):
            if facet == "kind" and typ in ("surface", "hdri", "decal", "atlas"):
                continue  # a "Rock Wall" texture is a stone material, not a rock model
            if facet == "condition" and tag in GENERIC_CONDITION and t not in own:
                continue  # vendors tag nearly everything "old" / "clean": only trust the asset's own name for these
            tags[facet].add(tag)
    # botanical names: first word of the name, or any folder word, is a genus
    first = re.split(r"[\s_]+", name.strip())[0].lower() if name.strip() else ""
    grp = GENUS.get(first) or next((GENUS[w] for w in _tokens(name) if w in GENUS), None)
    if grp and typ not in ("surface", "hdri", "decal", "atlas"):
        kinds, settings = PLANT_GROUP[grp]
        tags["kind"] |= kinds
        tags["setting"] |= settings
    if typ == "plant" or tags["kind"] & {"tree", "shrub", "grass", "flower", "palm", "fern", "succulent"}:
        tags["kind"].add("plant") if typ != "surface" else None
    if "snow" in tags["material"] and typ not in ("surface",):
        tags["setting"].add("winter")
    sz = _size(asset) if typ in ("3d", "plant", "scene", "splat") else None
    if sz:
        tags["size"].add(sz)
    return {f: sorted(v) for f, v in tags.items() if v}


def flat(smart):
    return sorted({t for v in smart.values() for t in v})
