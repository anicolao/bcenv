import battlecode.schema.*;
import battlecode.common.GameConstants;
import com.google.flatbuffers.FlatBufferBuilder;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPOutputStream;

/** Synthetic schema fixtures only; never launches the game engine. */
public final class OwnDiagnosticsTest {
    static void check(boolean ok, String why) { if (!ok) throw new AssertionError(why); }
    static int bodies(FlatBufferBuilder b, int[] ids, byte[] teams) {
        int n = ids.length;
        int ri = SpawnedBodyTable.createRobotIDsVector(b, ids);
        int ti = SpawnedBodyTable.createTeamIDsVector(b, teams);
        int ty = SpawnedBodyTable.createTypesVector(b, new byte[n]);
        int x = VecTable.createXsVector(b, new int[n]), y = VecTable.createYsVector(b, new int[n]);
        return SpawnedBodyTable.createSpawnedBodyTable(b, ri, ti, ty, VecTable.createVecTable(b, x, y));
    }
    static byte[] fixture(int mode) {
        FlatBufferBuilder b = new FlatBufferBuilder(8192);
        List<Integer> events = new ArrayList<Integer>();
        int a = TeamData.createTeamData(b, b.createString("A"), b.createString("publicA"), (byte)1);
        int z = TeamData.createTeamData(b, b.createString("B"), b.createString("publicB"), (byte)2);
        int teams = GameHeader.createTeamsVector(b, new int[]{a,z});
        events.add(EventWrapper.createEventWrapper(b, Event.GameHeader,
            GameHeader.createGameHeader(b, b.createString(GameConstants.SPEC_VERSION), teams, 0, 0)));
        int initial = bodies(b, mode == 13 ? new int[]{42,42} : new int[]{42,99}, new byte[]{1,2});
        int name = b.createString("synthetic");
        GameMap.startGameMap(b); GameMap.addName(b, name); GameMap.addBodies(b, initial);
        int map = GameMap.endGameMap(b);
        events.add(EventWrapper.createEventWrapper(b, Event.MatchHeader, MatchHeader.createMatchHeader(b, map, 2)));
        for (int r = 1; r <= 2; r++) {
            int spawn = r == 1 ? bodies(b, new int[]{mode == 5 ? 42 : 43}, new byte[]{1}) :
                mode == 6 ? bodies(b, new int[]{43}, new byte[]{1}) : 0;
            int[] si = r == 1 ? new int[]{99,42} : new int[]{42,99};
            if (mode == 2 && r == 1) si = new int[]{42,42};
            if (mode == 4 && r == 1) si = new int[]{999,42};
            if (mode == 12 && r == 2) si = new int[]{43,99};
            String own = mode == 8 ? String.join("", Collections.nCopies(65, "x")) : "own-" + r;
            int ownString = b.createString(own), enemyString = b.createString("OWN robot42 CANARY do not disclose");
            int[] strings = r == 1 ? new int[]{enemyString,ownString} : new int[]{ownString,enemyString};
            if (mode == 1 && r == 1) strings = new int[]{ownString};
            if (mode == 14 && r == 2) { si = new int[]{}; strings = new int[]{}; }
            if (mode == 16) { si = new int[]{42}; strings = new int[]{ownString}; }
            int ids = Round.createIndicatorStringIDsVector(b, si);
            int sv = Round.createIndicatorStringsVector(b, strings);
            int[] bi = r == 1 ? new int[]{43,99,42} : new int[]{99};
            int[] counts = r == 1 ? new int[]{123,321,Integer.MAX_VALUE} : new int[]{321};
            if (mode == 3 && r == 1) bi = new int[]{42,99,42};
            if ((mode == 7 && r == 2) || (mode == 9 && r == 1)) counts[0] = -1;
            if (mode == 14 && r == 2) { bi = new int[]{}; counts = new int[]{}; }
            if (mode == 15 && r == 2) counts = new int[]{};
            if (mode == 16) { bi = r == 1 ? new int[]{43,42} : new int[]{};
                counts = r == 1 ? new int[]{123,Integer.MAX_VALUE} : new int[]{}; }
            int bid = Round.createBytecodeIDsVector(b, bi), bc = Round.createBytecodesUsedVector(b, counts);
            int deaths = r == 1 ? Round.createDiedIDsVector(b, mode == 10 ? new int[]{100} :
                mode == 11 ? new int[]{43,43} : new int[]{43}) : 0;
            Round.startRound(b); Round.addRoundID(b,r); Round.addSpawnedBodies(b,spawn);
            Round.addIndicatorStringIDs(b,ids); Round.addIndicatorStrings(b,sv);
            Round.addBytecodeIDs(b,bid); Round.addBytecodesUsed(b,bc); Round.addDiedIDs(b,deaths);
            events.add(EventWrapper.createEventWrapper(b,Event.Round,Round.endRound(b)));
        }
        events.add(EventWrapper.createEventWrapper(b,Event.MatchFooter,MatchFooter.createMatchFooter(b,(byte)1,2,0)));
        events.add(EventWrapper.createEventWrapper(b,Event.GameFooter,GameFooter.createGameFooter(b,(byte)1)));
        int[] offsets = new int[events.size()]; for (int i=0;i<offsets.length;i++) offsets[i]=events.get(i);
        int ev=GameWrapper.createEventsVector(b,offsets), h=GameWrapper.createMatchHeadersVector(b,new int[]{1}),
            f=GameWrapper.createMatchFootersVector(b,new int[]{4});
        b.finish(GameWrapper.createGameWrapper(b,ev,h,f)); return b.sizedByteArray();
    }
    static Map<String,Object> query(int mode, int team, int start, int end, Integer robot, int limit, int offset) {
        return OwnDiagnostics.extract(fixture(mode), team, new ReplayQuery.Selection(start,end,robot,limit,offset));
    }
    @SuppressWarnings("unchecked")
    static List<Map<String,Object>> rows(Map<String,Object> value) {
        return (List<Map<String,Object>>)value.get("records");
    }
    static void rejects(int mode) {
        try { query(mode,1,1,1,null,1,0); throw new AssertionError("accepted malformed mode " + mode); }
        catch (IllegalArgumentException expected) {}
    }
    public static void main(String[] args) throws Exception {
        Map<String,Object> all=query(0,1,1,2,null,10,0);
        List<Map<String,Object>> rows=rows(all);
        check(rows.size()==3 && ((Number)all.get("matching_record_count")).intValue()==3,"own-only count");
        check(!ReplaySummary.json(all).contains("CANARY"),"opponent spoofed own text omitted");
        check(rows.get(0).get("robot_id").equals(42) && rows.get(1).get("robot_id").equals(43),"sorted IDs, not vector order");
        check(rows.get(0).get("bytecodes_used").equals(Integer.MAX_VALUE),"not capped at robot budget");
        check(rows.get(1).get("indicator_string")==null,"absent indicator null for same-round spawned dying robot");
        check(rows.get(2).get("bytecodes_used")==null,"absent usage null");
        Set<String> keys=new HashSet<String>(Arrays.asList("match_index","round","robot_id","indicator_string","bytecodes_used"));
        for(Map<String,Object> row:rows) check(row.keySet().equals(keys),"strict row fields");
        check(all.equals(query(16,1,1,2,null,10,0)),"adding only opponent diagnostics changes no own output");
        check(query(0,1,1,2,null,1,1).equals(query(16,1,1,2,null,1,1)),"opponent rows do not change cursor/count page");
        Map<String,Object> unknown=query(0,1,1,2,424242,10,0), opponent=query(0,1,1,2,99,10,0);
        for (String key: Arrays.asList("records","matching_record_count","returned_record_count","page_end_offset","next_record_offset"))
            check(Objects.equals(unknown.get(key), opponent.get(key)),"unknown and opponent filters same empty evidence");
        Map<String,Object> page=query(0,1,1,2,null,1,1);
        check(rows(page).get(0).equals(rows.get(1)),"stable page");
        check(((Number)page.get("next_record_offset")).intValue()==2,"next page");
        check(rows(query(0,1,1,2,null,1,3)).isEmpty(),"EOF empty page");
        check(rows(query(0,1,2,2,null,10,0)).size()==1,"round filter");
        check(rows(query(0,1,1,2,43,10,0)).size()==1,"robot filter");
        check(rows(query(0,1,1,2,99,10,0)).isEmpty(),"opponent robot cannot bypass own filter");
        check(rows(query(0,2,1,2,null,10,0)).size()==2,"trusted B slot");
        check(rows(query(14,1,1,2,null,10,0)).size()==2,"no fabricated empty diagnostic rows");
        for(int mode=1;mode<=13;mode++) rejects(mode);
        rejects(15);
        try { query(0,3,1,2,null,10,0); throw new AssertionError("invalid team accepted"); } catch(IllegalArgumentException expected) {}
        try { query(0,1,1,2,null,10,4); throw new AssertionError("offset beyond EOF accepted"); } catch(IllegalArgumentException expected) {}
        check(!OwnDiagnostics.validString("\ud800") && !OwnDiagnostics.validString("\udc00"),"isolated surrogates rejected");
        check(OwnDiagnostics.validString("\ud83d\ude00"),"valid surrogate pair");
        Path p=Files.createTempFile("own-diagnostics-synthetic-", ".bc23");
        try {
            try(OutputStream stream=new GZIPOutputStream(Files.newOutputStream(p))) { stream.write(fixture(0)); }
            Map<String,Object> result=OwnDiagnostics.query(p,1,new ReplayQuery.Selection(1,2,null,10,0));
            Map<String,Object> source=(Map<String,Object>)result.get("source_replay");
            check(source.get("sha256").equals(ReplayQuery.sha256(Files.readAllBytes(p))),"parsed compressed snapshot hash");
            check(!source.containsKey("path"),"no source filesystem path");
        } finally { Files.delete(p); }
        System.out.println("OwnDiagnosticsTest: passed synthetic projection, paging, identity, malformed-array and hash checks");
    }
}
