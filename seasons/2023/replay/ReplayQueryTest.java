import battlecode.schema.*;
import battlecode.common.GameConstants;
import com.google.flatbuffers.FlatBufferBuilder;
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPOutputStream;

/** Official-schema synthetic fixtures, independently specified expected raw rows. */
public final class ReplayQueryTest {
    static void check(boolean ok, String why) { if (!ok) throw new AssertionError(why); }
    static int body(FlatBufferBuilder b, int id, byte team, byte type, int x, int y) {
        int ids = SpawnedBodyTable.createRobotIDsVector(b, new int[]{id});
        int teams = SpawnedBodyTable.createTeamIDsVector(b, new byte[]{team});
        int types = SpawnedBodyTable.createTypesVector(b, new byte[]{type});
        int xs = VecTable.createXsVector(b, new int[]{x});
        int ys = VecTable.createYsVector(b, new int[]{y});
        return SpawnedBodyTable.createSpawnedBodyTable(b, ids, teams, types, VecTable.createVecTable(b, xs, ys));
    }
    static byte[] fixture(boolean multiple, boolean gap, boolean footer, boolean badLateAction) {
        FlatBufferBuilder b = new FlatBufferBuilder(8192);
        List<Integer> events = new ArrayList<Integer>(), heads = new ArrayList<Integer>(), feet = new ArrayList<Integer>();
        int a = TeamData.createTeamData(b, b.createString("A"), b.createString("alpha"), (byte)1);
        int z = TeamData.createTeamData(b, b.createString("B"), b.createString("beta"), (byte)2);
        int teams = GameHeader.createTeamsVector(b, new int[]{z, a});
        events.add(EventWrapper.createEventWrapper(b, Event.GameHeader, GameHeader.createGameHeader(b, b.createString(GameConstants.SPEC_VERSION), teams, 0, 0)));
        for (int match = 0; match < (multiple ? 2 : 1); match++) {
            int initial = body(b, 42, match == 0 ? (byte)1 : (byte)2, match == 0 ? BodyType.HEADQUARTERS : BodyType.LAUNCHER, 5, 6);
            int name = b.createString("synthetic-" + match);
            GameMap.startGameMap(b); GameMap.addName(b, name); GameMap.addBodies(b, initial);
            int map = GameMap.endGameMap(b);
            heads.add(events.size());
            events.add(EventWrapper.createEventWrapper(b, Event.MatchHeader, MatchHeader.createMatchHeader(b, map, 45)));
            for (int round = 1; round <= 45; round++) {
                if (gap && round == 34) continue;
                int spawned = round == 35 ? body(b, 43, (byte)2, BodyType.CARRIER, 7, 8) : 0;
                int[] robotIDs = round >= 35 && round <= 40 ? new int[]{42, 43} : new int[]{42};
                int[] xs = robotIDs.length == 2 ? new int[]{round, round + 1} : new int[]{round};
                int[] ys = robotIDs.length == 2 ? new int[]{round + 2, round + 3} : new int[]{round + 2};
                int moved = Round.createMovedIDsVector(b, robotIDs);
                int x = VecTable.createXsVector(b, xs), y = VecTable.createYsVector(b, ys);
                int loc = VecTable.createVecTable(b, x, y);
                int actions = Round.createActionsVector(b, robotIDs.length == 2 ? new byte[]{Action.CHANGE_ADAMANTIUM, Action.PICK_UP_RESOURCE} : new byte[]{badLateAction && round == 45 ? (byte)99 : Action.CHANGE_ADAMANTIUM});
                int targets = Round.createActionTargetsVector(b, robotIDs.length == 2 ? new int[]{-round, 9000 + round} : new int[]{-round});
                int actionIDs = Round.createActionIDsVector(b, robotIDs);
                int teamIDs = Round.createTeamIDsVector(b, new int[]{2, 1});
                int ad = Round.createTeamAdChangesVector(b, new int[]{-round, round});
                int mn = Round.createTeamMnChangesVector(b, new int[]{0, 2});
                int ex = Round.createTeamExChangesVector(b, new int[]{1, 0});
                int deaths = Round.createDiedIDsVector(b, round == 40 ? new int[]{43} : round == 41 ? new int[]{999} : new int[]{});
                Round.startRound(b); Round.addRoundID(b, round); Round.addSpawnedBodies(b, spawned);
                Round.addMovedIDs(b, moved); Round.addMovedLocs(b, loc);
                Round.addActionIDs(b, actionIDs); Round.addActions(b, actions); Round.addActionTargets(b, targets);
                Round.addTeamIDs(b, teamIDs); Round.addTeamAdChanges(b, ad); Round.addTeamMnChanges(b, mn); Round.addTeamExChanges(b, ex);
                Round.addDiedIDs(b, deaths);
                events.add(EventWrapper.createEventWrapper(b, Event.Round, Round.endRound(b)));
            }
            feet.add(events.size());
            events.add(EventWrapper.createEventWrapper(b, Event.MatchFooter, MatchFooter.createMatchFooter(b, (byte)2, 45, 0)));
        }
        if (footer) events.add(EventWrapper.createEventWrapper(b, Event.GameFooter, GameFooter.createGameFooter(b, (byte)2)));
        int ev = GameWrapper.createEventsVector(b, ints(events));
        int h = GameWrapper.createMatchHeadersVector(b, ints(heads)), f = GameWrapper.createMatchFootersVector(b, ints(feet));
        b.finish(GameWrapper.createGameWrapper(b, ev, h, f));
        return b.sizedByteArray();
    }
    static byte[] longFixture() {
        FlatBufferBuilder b = new FlatBufferBuilder(262144);
        int a = TeamData.createTeamData(b, b.createString("A"), b.createString("alpha"), (byte)1);
        int z = TeamData.createTeamData(b, b.createString("B"), b.createString("beta"), (byte)2);
        int teams = GameHeader.createTeamsVector(b, new int[]{a, z});
        int header = EventWrapper.createEventWrapper(b, Event.GameHeader,
            GameHeader.createGameHeader(b, b.createString(GameConstants.SPEC_VERSION), teams, 0, 0));
        int initial = body(b, 42, (byte)1, BodyType.HEADQUARTERS, 5, 6);
        int name = b.createString("synthetic-long");
        GameMap.startGameMap(b); GameMap.addName(b, name); GameMap.addBodies(b, initial);
        int match = EventWrapper.createEventWrapper(b, Event.MatchHeader,
            MatchHeader.createMatchHeader(b, GameMap.endGameMap(b), 1));
        int[] ids = new int[12050], targets = new int[12050]; byte[] actions = new byte[12050];
        Arrays.fill(ids, 42); Arrays.fill(targets, -1); Arrays.fill(actions, Action.CHANGE_HEALTH);
        actions[12049] = Action.DIE_EXCEPTION; targets[12049] = 0;
        int ai = Round.createActionIDsVector(b, ids), ac = Round.createActionsVector(b, actions);
        int at = Round.createActionTargetsVector(b, targets);
        int ti = Round.createTeamIDsVector(b, new int[]{2, 1});
        int ad = Round.createTeamAdChangesVector(b, new int[]{-1, 1});
        int mn = Round.createTeamMnChangesVector(b, new int[]{0, 2});
        int ex = Round.createTeamExChangesVector(b, new int[]{1, 0});
        int deaths = Round.createDiedIDsVector(b, new int[]{42});
        Round.startRound(b); Round.addRoundID(b, 1); Round.addActionIDs(b, ai);
        Round.addActions(b, ac); Round.addActionTargets(b, at); Round.addTeamIDs(b, ti);
        Round.addTeamAdChanges(b, ad); Round.addTeamMnChanges(b, mn); Round.addTeamExChanges(b, ex);
        Round.addDiedIDs(b, deaths);
        int round = EventWrapper.createEventWrapper(b, Event.Round, Round.endRound(b));
        int footer = EventWrapper.createEventWrapper(b, Event.MatchFooter,
            MatchFooter.createMatchFooter(b, (byte)2, 1, 0));
        int gameFooter = EventWrapper.createEventWrapper(b, Event.GameFooter,
            GameFooter.createGameFooter(b, (byte)2));
        int events = GameWrapper.createEventsVector(b, new int[]{header, match, round, footer, gameFooter});
        int heads = GameWrapper.createMatchHeadersVector(b, new int[]{1});
        int feet = GameWrapper.createMatchFootersVector(b, new int[]{3});
        b.finish(GameWrapper.createGameWrapper(b, events, heads, feet));
        return b.sizedByteArray();
    }
    static void pageTests() {
        for (boolean multiple : new boolean[]{false, true}) {
            byte[] data = fixture(multiple, false, true, false);
            Map<String,Object> complete = ReplayQuery.extract(data, new ReplayQuery.Selection(35, 40, 43, 100));
            int count = rows(complete).size();
            for (int offset = 0; offset <= count; offset++) {
                for (int limit : new int[]{1, 2, 5, 100}) {
                    Map<String,Object> out = ReplayQuery.extract(data, new ReplayQuery.Selection(35, 40, 43, limit, offset));
                    int end = Math.min(count, offset + limit);
                    check(out.get("format").equals(ReplayQuery.PAGE_FORMAT), "explicit v2");
                    check(rows(out).equals(rows(complete).subList(offset, end)), "exact page slice at every offset");
                    check(out.get("matching_counts_by_kind").equals(complete.get("matching_counts_by_kind")), "whole-window counts");
                    check(out.get("matches").equals(complete.get("matches")), "whole-window match coverage");
                    check(out.get("page_records_complete").equals(true), "bounded slice complete");
                    check(out.get("page_end_offset").equals((long)end), "exact page end");
                    check(Objects.equals(out.get("next_record_offset"), end < count ? (long)end : null), "next source offset");
                    check(out.get("selected_records_complete").equals(offset == 0 && end == count), "window completeness separate");
                }
            }
            rejects(() -> ReplayQuery.extract(data, new ReplayQuery.Selection(35, 40, 43, 1, count + 1)), "past actual EOF");
        }
        byte[] large = longFixture();
        List<Map<String,Object>> collected = new ArrayList<Map<String,Object>>();
        int offset = 0;
        while (true) {
            Map<String,Object> out = ReplayQuery.extract(large, new ReplayQuery.Selection(1, 1, null, 777, offset));
            check(out.get("matching_record_count").equals(12053L), "long full-window count");
            check(rows(out).size() <= 777, "materialized page bound");
            collected.addAll(rows(out));
            if (out.get("next_record_offset") == null) break;
            offset = ((Long)out.get("next_record_offset")).intValue();
        }
        check(collected.size() == 12053, "all long rows recovered");
        for (int i = 0; i < 12050; i++) {
            Map<String,Object> row = collected.get(i + 2);
            check(row.get("kind").equals("action") && row.get("row_index").equals(i), "no long gaps/duplicates");
            check(row.get("raw_target").equals(i == 12049 ? 0 : -1), "signed adverse evidence preserved");
        }
        check(collected.get(12051).get("action").equals("DIE_EXCEPTION") &&
            collected.get(12052).get("kind").equals("death"), "late exception/death beyond old10k cap");
        Map<String,Object> late = ReplayQuery.extract(large, new ReplayQuery.Selection(1, 1, null, 10, 12050));
        check(rows(late).size() == 3 && rows(late).get(2).get("team_id").equals(1), "identity processed before late offset");
        check(ReplayQuery.extract(fixture(false, false, false, false),
            new ReplayQuery.Selection(35, 40, 43, 1, 25)).get("source_stream_complete").equals(false), "incomplete late page stays incomplete");
        rejects(() -> ReplayQuery.extract(fixture(false, false, true, true),
            new ReplayQuery.Selection(35, 40, 43, 1, 25)), "bad event beyond late page");
        try {
            ReplayQuery.extract(fixture(false, false, true, true), new ReplayQuery.Selection(1, 1, null, 1, 5000000));
            throw new AssertionError("accepted corrupt source at out-of-range offset");
        } catch (RuntimeException expected) {
            check(!expected.getMessage().contains("offset"), "full validation precedes actual EOF check");
        }
        rejects(() -> new ReplayQuery.Selection(0, 256, null, 1, 0), "page span cap");
        rejects(() -> new ReplayQuery.Selection(0, 1, null, 1, -1), "negative offset");
        rejects(() -> new ReplayQuery.Selection(0, 1, null, 1, 5000001), "offset ceiling");
        Map<String,Object> empty = ReplayQuery.extract(large, new ReplayQuery.Selection(2, 2, null, 1, 0));
        check(rows(empty).isEmpty() && empty.get("next_record_offset") == null, "empty window terminal");
        check(ReplayQuery.extract(large, new ReplayQuery.Selection(0, 300, null, 1)).get("format").equals(ReplayQuery.FORMAT), "v1 span unchanged");
    }
    static int[] ints(List<Integer> source) {
        int[] result = new int[source.size()]; for (int i = 0; i < result.length; i++) result[i] = source.get(i); return result;
    }
    @SuppressWarnings("unchecked") static List<Map<String,Object>> rows(Map<String,Object> out) { return (List<Map<String,Object>>)(List<?>)out.get("records"); }
    @SuppressWarnings("unchecked") static List<Map<String,Object>> matches(Map<String,Object> out) { return (List<Map<String,Object>>)(List<?>)out.get("matches"); }
    static void rejects(Runnable fn, String why) { try { fn.run(); throw new AssertionError("accepted " + why); } catch (RuntimeException expected) { } }
    static void write(Path path, byte[] data) throws Exception { try (GZIPOutputStream gz = new GZIPOutputStream(Files.newOutputStream(path))) { gz.write(data); } }
    public static void main(String[] args) throws Exception {
        byte[] data = fixture(false, false, true, false);
        if (args.length == 2 && "--write-long-fixture".equals(args[0])) { write(Paths.get(args[1]), longFixture()); return; }
        if (args.length == 2 && "--write-fixture".equals(args[0])) { write(Paths.get(args[1]), data); return; }
        Map<String,Object> out = ReplayQuery.extract(data, new ReplayQuery.Selection(35, 40, 43, 100));
        check(out.get("matching_record_count").equals(26L), "spawn + 12 team rows + 6 moves + 6 actions + death");
        check(out.get("selected_records_complete").equals(true), "uncapped selection complete");
        check(matches(out).get(0).get("all_requested_positive_rounds_present").equals(true), "inclusive coverage");
        // Independently look up every returned row's official source vector and index.
        GameWrapper raw = GameWrapper.getRootAsGameWrapper(ByteBuffer.wrap(data));
        for (Map<String,Object> row : rows(out)) {
            Round r = (Round)raw.events((Integer)row.get("event_index")).e(new Round());
            int i = (Integer)row.get("row_index"), round = (Integer)row.get("round");
            check(round >= 35 && round <= 40 && r.roundID() == round, "inclusive exact round");
            String kind = (String)row.get("kind");
            if (kind.equals("team_resource_delta")) {
                check(row.get("team_id").equals(r.teamIDs(i)), "raw team ID");
                check(row.get("adamantium_delta").equals(r.teamAdChanges(i)), "raw team delta");
            } else {
                check(row.get("robot_id").equals(43), "exact robot filter");
                check(row.get("body_type").equals("CARRIER") && row.get("team_id").equals(2), "joined spawn identity");
                check(row.get("identity_observed_round").equals(35), "identity source round");
                if (kind.equals("action")) check(row.get("raw_target").equals(r.actionTargets(i)) && row.get("action").equals(Action.name(r.actions(i))), "raw action");
                if (kind.equals("movement")) check(row.get("x").equals(r.movedLocs().xs(i)) && row.get("y").equals(r.movedLocs().ys(i)), "raw movement");
                if (kind.equals("spawn")) check(row.get("x").equals(r.spawnedBodies().locs().xs(i)), "raw spawn");
                if (kind.equals("death")) check(row.get("robot_id").equals(r.diedIDs(i)), "raw death");
            }
        }
        Map<String,Object> capped = ReplayQuery.extract(data, new ReplayQuery.Selection(35, 40, 43, 3));
        check(capped.get("truncated").equals(true) && capped.get("selected_records_complete").equals(false), "explicit truncation");
        check(capped.get("matching_record_count").equals(26L) && rows(capped).size() == 3, "count continues after cap");
        check(rows(capped).equals(rows(out).subList(0, 3)), "deterministic prefix");
        check(ReplayQuery.extract(data, new ReplayQuery.Selection(35, 40, 43, 26)).get("truncated").equals(false), "exact limit is not truncated");
        out = ReplayQuery.extract(data, new ReplayQuery.Selection(45, 45, 42, 10));
        check(rows(out).size() == 4 && rows(out).get(3).get("raw_target").equals(-45), "late signed raw target beyond first32");
        out = ReplayQuery.extract(data, new ReplayQuery.Selection(41, 41, 999, 10));
        check(rows(out).size() == 3 && rows(out).get(2).get("body_type") == null, "unknown death identity remains null");
        out = ReplayQuery.extract(fixture(true, false, true, false), new ReplayQuery.Selection(0, 0, 42, 10));
        check(rows(out).size() == 2 && rows(out).get(0).get("kind").equals("initial_body"), "initial round0");
        check(rows(out).get(1).get("body_type").equals("LAUNCHER") && rows(out).get(1).get("match_index").equals(1), "match identity reset");
        out = ReplayQuery.extract(data, new ReplayQuery.Selection(46, 47, null, 10));
        check(rows(out).isEmpty() && out.get("selected_records_complete").equals(true), "empty selection");
        check(matches(out).get(0).get("all_requested_positive_rounds_present").equals(false), "absent rounds explicit");
        out = ReplayQuery.extract(fixture(false, true, true, false), new ReplayQuery.Selection(33, 35, null, 100));
        check(matches(out).get(0).get("all_requested_positive_rounds_present").equals(false), "gap coverage explicit");
        check(ReplayQuery.extract(fixture(false, false, false, false), new ReplayQuery.Selection(1, 1, null, 1)).get("source_stream_complete").equals(false), "missing game footer explicit");
        rejects(() -> ReplayQuery.extract(fixture(false, false, true, true), new ReplayQuery.Selection(1, 1, null, 1)), "invalid event outside query and beyond cap");
        rejects(() -> ReplayQuery.extract(new byte[]{1,2,3}, new ReplayQuery.Selection(0, 1, null, 1)), "truncated flatbuffer");
        rejects(() -> new ReplayQuery.Selection(-1, 1, null, 1), "negative round");
        rejects(() -> new ReplayQuery.Selection(2, 1, null, 1), "reversed range");
        rejects(() -> new ReplayQuery.Selection(0, 1, -1, 1), "negative robot");
        rejects(() -> new ReplayQuery.Selection(0, 1, null, 0), "zero cap");
        rejects(() -> new ReplayQuery.Selection(0, 1, null, 10001), "large cap");
        Path temp = Files.createTempFile("bc23-query-test-", ".bc23");
        try {
            write(temp, data); byte[] compressed = Files.readAllBytes(temp);
            out = ReplayQuery.query(temp, new ReplayQuery.Selection(35, 40, 43, 3));
            check(ReplaySummary.json(out.get("source_replay")).contains(ReplayQuery.sha256(compressed)), "exact compressed provenance");
            check(Arrays.equals(compressed, Files.readAllBytes(temp)), "input unchanged");
            Files.write(temp, Arrays.copyOf(compressed, compressed.length - 5));
            try { ReplayQuery.query(temp, new ReplayQuery.Selection(0, 1, null, 1)); throw new AssertionError("accepted truncated gzip"); } catch (IOException expected) { }
        } finally { Files.deleteIfExists(temp); }
        pageTests();
        System.out.println("ReplayQueryTest: PASS (late raw vectors, identity, inclusive filters, cap, multi-match, gaps, malformed tails, provenance)");
    }
}
