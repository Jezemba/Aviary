"""Design space metadata definitions for the Aviary MCP server."""

DESIGN_PARAMETERS = [
    {
        "name": "Aircraft.Wing.ASPECT_RATIO",
        "display_name": "Wing Aspect Ratio",
        "category": "wing",
        "current_default": 11.22,
        "units": "unitless",
        "min": 7.0,
        "max": 14.0,
        "description": "Wing aspect ratio (span^2 / area). Higher values reduce induced drag but increase structural weight.",
    },
    {
        "name": "Aircraft.Wing.AREA",
        "display_name": "Wing Reference Area",
        "category": "wing",
        "current_default": 124.6,
        "units": "m^2",
        "min": 100.0,
        "max": 160.0,
        "description": "Wing reference (planform) area. Larger wings produce more lift but more drag and weight.",
    },
    {
        "name": "Aircraft.Wing.SPAN",
        "display_name": "Wingspan",
        "category": "wing",
        "current_default": 37.35,
        "units": "m",
        "min": 28.0,
        "max": 48.0,
        "description": "Wing tip-to-tip span. Coupled with area and aspect ratio via AR = span^2 / area.",
    },
    {
        "name": "Aircraft.Wing.SWEEP",
        "display_name": "Wing Sweep (quarter-chord)",
        "category": "wing",
        "current_default": 25.0,
        "units": "deg",
        "min": 15.0,
        "max": 40.0,
        "description": "Quarter-chord sweep angle. Higher sweep delays drag rise at transonic speeds.",
    },
    {
        "name": "Aircraft.Wing.TAPER_RATIO",
        "display_name": "Wing Taper Ratio",
        "category": "wing",
        "current_default": 0.278,
        "units": "unitless",
        "min": 0.15,
        "max": 0.45,
        "description": "Ratio of tip chord to root chord. Lower values improve span loading efficiency.",
    },
    {
        "name": "Aircraft.Fuselage.LENGTH",
        "display_name": "Fuselage Length",
        "category": "fuselage",
        "current_default": 37.79,
        "units": "m",
        "min": 28.0,
        "max": 50.0,
        "description": "Overall fuselage length. Affects wetted area, friction drag, and cabin capacity.",
    },
    {
        "name": "Aircraft.Fuselage.MAX_HEIGHT",
        "display_name": "Fuselage Max Height",
        "category": "fuselage",
        "current_default": 4.06,
        "units": "m",
        "min": 3.0,
        "max": 5.5,
        "description": "Maximum fuselage cross-section height.",
    },
    {
        "name": "Aircraft.Fuselage.MAX_WIDTH",
        "display_name": "Fuselage Max Width",
        "category": "fuselage",
        "current_default": 3.76,
        "units": "m",
        "min": 3.0,
        "max": 5.5,
        "description": "Maximum fuselage cross-section width.",
    },
    {
        "name": "Aircraft.Engine.SCALED_SLS_THRUST",
        "display_name": "Sea-Level Static Thrust (per engine)",
        "category": "engine",
        "current_default": 28928.1,
        "units": "lbf",
        "min": 20000,
        "max": 45000,
        "description": "Scaled sea-level static thrust per engine. Computed as SCALE_FACTOR * reference_sls_thrust (28928.1 lbf). Read-only output — modify via SCALE_FACTOR.",
    },
    {
        "name": "Aircraft.Engine.SCALE_FACTOR",
        "display_name": "Engine Scale Factor",
        "category": "engine",
        "current_default": 1.0,
        "units": "unitless",
        "min": 0.8,
        "max": 1.5,
        "description": "Engine scale factor relative to the reference engine. Scales thrust, mass, and fuel flow.",
    },
]

COUPLING_NOTE = (
    "Wing area, span, and aspect ratio satisfy AR = span^2 / area. "
    "Callers should change at most one of {AREA, SPAN, ASPECT_RATIO} per call "
    "to set_aircraft_parameters, treating the others as derived. Setting all three "
    "to independent values will produce an internally inconsistent geometry."
)

AIRCRAFT_NAME = "Single-Aisle Transport (737/A320 class)"

# Map from PRD-style dotted names to aviary colon-separated variable strings
VARIABLE_NAME_MAP = {
    "Aircraft.Wing.ASPECT_RATIO": "aircraft:wing:aspect_ratio",
    "Aircraft.Wing.AREA": "aircraft:wing:area",
    "Aircraft.Wing.SPAN": "aircraft:wing:span",
    "Aircraft.Wing.SWEEP": "aircraft:wing:sweep",
    "Aircraft.Wing.TAPER_RATIO": "aircraft:wing:taper_ratio",
    "Aircraft.Fuselage.LENGTH": "aircraft:fuselage:length",
    "Aircraft.Fuselage.MAX_HEIGHT": "aircraft:fuselage:max_height",
    "Aircraft.Fuselage.MAX_WIDTH": "aircraft:fuselage:max_width",
    "Aircraft.Engine.SCALED_SLS_THRUST": "aircraft:engine:scaled_sls_thrust",
    "Aircraft.Engine.SCALE_FACTOR": "aircraft:engine:scale_factor",
}

VALID_PARAMETER_NAMES = set(VARIABLE_NAME_MAP.keys())


def get_design_space(category="all"):
    """Return design space parameters, optionally filtered by category."""
    if category == "all":
        params = DESIGN_PARAMETERS
    else:
        params = [p for p in DESIGN_PARAMETERS if p["category"] == category]

    return {
        "success": True,
        "parameters": params,
        "aircraft_name": AIRCRAFT_NAME,
        "coupling_note": COUPLING_NOTE,
    }
