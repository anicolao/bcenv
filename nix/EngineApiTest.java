import battlecode.common.*;

/** Released-engine API/constant regression checks; not a player or strategy. */
public final class EngineApiTest {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) throws Exception {
        check(args.length == 1 && args[0].equals(GameConstants.SPEC_VERSION), "pinned spec version");
        boolean finalRules = "3.0.13".equals(args[0]);
        check(finalRules || "2.0.3".equals(args[0]), "explicitly supported engine profile");
        check(RobotType.LAUNCHER.buildCostMana == (finalRules ? 45 : 60), "launcher cost");
        check(RobotType.LAUNCHER.damage == (finalRules ? 20 : 30), "launcher damage");
        check(RobotType.LAUNCHER.movementCooldown == (finalRules ? 20 : 15), "launcher movement cooldown");
        check(RobotType.CARRIER.actionCooldown == (finalRules ? 10 : 8), "carrier action cooldown");
        check(RobotType.AMPLIFIER.buildCostMana == (finalRules ? 15 : 30), "amplifier cost");
        check(Anchor.STANDARD.adamantiumCost == (finalRules ? 80 : 100), "standard anchor adamantium cost");
        check(Anchor.STANDARD.manaCost == (finalRules ? 80 : 100), "standard anchor mana cost");
        check(Anchor.STANDARD.healingAmount == (finalRules ? 4 : 1), "standard anchor healing");
        check(Anchor.ACCELERATING.healingAmount == (finalRules ? 6 : 2), "accelerating anchor healing");
        check(GameConstants.UPGRADE_TO_ELIXIR == (finalRules ? 600 : 700), "elixir conversion cost");
        check(GameConstants.UPGRADE_WELL_AMOUNT == (finalRules ? 1400 : 1700), "well upgrade cost");
        check(RobotType.HEADQUARTERS.bytecodeLimit == 20000, "headquarters bytecode budget");
        check(RobotType.CARRIER.bytecodeLimit == 12500, "carrier bytecode budget");
        check(RobotType.LAUNCHER.bytecodeLimit == 10000, "launcher bytecode budget");
        RobotController.class.getMethod("senseCloud", MapLocation.class);
        RobotController.class.getMethod("senseNearbyCloudLocations", MapLocation.class, int.class);
        RobotController.class.getMethod("attack", MapLocation.class);
        RobotController.class.getMethod("getWeight");
        RobotController.class.getMethod("collectResource", MapLocation.class, int.class);
        System.out.println("EngineApiTest: PASS " + args[0] + " (released constants and API signatures; no gameplay simulation)");
    }
}
