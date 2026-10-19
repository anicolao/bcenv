import battlecode.schema.*;
import battlecode.common.GameConstants;
import com.google.flatbuffers.FlatBufferBuilder;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPOutputStream;

/** Dependency-free synthetic-schema tests; these are not claims of engine gameplay. */
public final class ReplaySummaryTest {
    static void check(boolean b,String message) { if(!b)throw new AssertionError(message); }
    static byte[] fixture(boolean gameFooter,boolean badParallel,boolean unknownAction) {
        FlatBufferBuilder b=new FlatBufferBuilder(1024);
        int a=TeamData.createTeamData(b,b.createString("quoted \"A\""),b.createString("alpha"),(byte)1);
        int z=TeamData.createTeamData(b,b.createString("B"),b.createString("beta"),(byte)2);
        int teams=GameHeader.createTeamsVector(b,new int[]{z,a}); // order must not determine slot
        int gh=GameHeader.createGameHeader(b,b.createString(GameConstants.SPEC_VERSION),teams,0,0);
        int e0=EventWrapper.createEventWrapper(b,Event.GameHeader,gh);
        int name=b.createString("synthetic");GameMap.startGameMap(b);GameMap.addName(b,name);int map=GameMap.endGameMap(b);
        int mh=MatchHeader.createMatchHeader(b,map,2000);int e1=EventWrapper.createEventWrapper(b,Event.MatchHeader,mh);
        int ids=Round.createTeamIDsVector(b,new int[]{1,2});
        int ad=Round.createTeamAdChangesVector(b,new int[]{5,-2});
        int mn=Round.createTeamMnChangesVector(b,new int[]{3,0});
        int ex=Round.createTeamExChangesVector(b,new int[]{0,1});
        int robot=Round.createActionIDsVector(b,new int[]{42});
        int actions=Round.createActionsVector(b,new byte[]{unknownAction?(byte)99:Action.PICK_UP_RESOURCE});
        int targets=Round.createActionTargetsVector(b,badParallel?new int[]{}:new int[]{123});
        int moved=Round.createMovedIDsVector(b,new int[]{42});
        int xs=VecTable.createXsVector(b,new int[]{4}),ys=VecTable.createYsVector(b,new int[]{6});
        int loc=VecTable.createVecTable(b,xs,ys);
        Round.startRound(b);Round.addRoundID(b,1);Round.addTeamIDs(b,ids);Round.addTeamAdChanges(b,ad);Round.addTeamMnChanges(b,mn);Round.addTeamExChanges(b,ex);
        Round.addActionIDs(b,robot);Round.addActions(b,actions);Round.addActionTargets(b,targets);Round.addMovedIDs(b,moved);Round.addMovedLocs(b,loc);
        int round=Round.endRound(b);int e2=EventWrapper.createEventWrapper(b,Event.Round,round);
        int mf=MatchFooter.createMatchFooter(b,(byte)2,1,0);int e3=EventWrapper.createEventWrapper(b,Event.MatchFooter,mf);
        int gf=GameFooter.createGameFooter(b,(byte)2);int e4=EventWrapper.createEventWrapper(b,Event.GameFooter,gf);
        int ev=GameWrapper.createEventsVector(b,gameFooter?new int[]{e0,e1,e2,e3,e4}:new int[]{e0,e1,e2,e3});
        int heads=GameWrapper.createMatchHeadersVector(b,new int[]{1}),foots=GameWrapper.createMatchFootersVector(b,new int[]{3});
        b.finish(GameWrapper.createGameWrapper(b,ev,heads,foots));return b.sizedByteArray();
    }
    static void invalid(byte[] data,String label) {
        try { ReplaySummary.extract(data);throw new AssertionError("accepted "+label); } catch(RuntimeException expected) { }
    }
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        byte[] data=fixture(true,false,false);Map<String,Object> out=ReplaySummary.extract(data);
        check("complete".equals(out.get("status")),"complete footer");check("B".equals(out.get("winner_slot")),"winner");
        List<Object> teams=(List<Object>)out.get("teams");check("B".equals(((Map<String,Object>)teams.get(0)).get("slot")),"slot comes from ID");
        Map<String,Object> match=(Map<String,Object>)((List<Object>)out.get("matches")).get(0);
        check(((Number)match.get("action_event_count")).intValue()==1,"action count");
        check(((Number)match.get("movement_event_count")).intValue()==1,"movement count");
        Map<String,Object> resource=(Map<String,Object>)((Map<String,Object>)match.get("team_resource_net_deltas")).get("A");
        check(((Number)resource.get("adamantium")).intValue()==5,"resource delta");
        check(ReplaySummary.json(out).contains("quoted \\\"A\\\""),"JSON escaping");
        check("incomplete".equals(ReplaySummary.extract(fixture(false,false,false)).get("status")),"missing footer");
        invalid(fixture(true,true,false),"mismatched arrays");invalid(fixture(true,false,true),"unknown action");invalid(new byte[]{1,2,3},"truncated flatbuffer");
        byte[] wrongVersion=data.clone();
        byte[] marker=GameConstants.SPEC_VERSION.getBytes("UTF-8");boolean replaced=false;
        for(int i=0;i<=wrongVersion.length-marker.length;i++) {
            boolean matchVersion=true;for(int j=0;j<marker.length;j++) if(wrongVersion[i+j]!=marker[j]) matchVersion=false;
            if(matchVersion) { wrongVersion[i]='9';replaced=true;break; }
        }
        check(replaced,"version fixture marker");invalid(wrongVersion,"unsupported spec version");
        Path temp=Files.createTempFile("bc23-replay-test-",".bc23");
        try {
            try(GZIPOutputStream gz=new GZIPOutputStream(Files.newOutputStream(temp))) { gz.write(data); }
            check(Arrays.equals(data,ReplaySummary.read(temp)),"gzip roundtrip");
            byte[] compressed=Files.readAllBytes(temp);Files.write(temp,Arrays.copyOf(compressed,compressed.length-5));
            try { ReplaySummary.read(temp);throw new AssertionError("accepted truncated gzip"); } catch(java.io.IOException expected) { }
        } finally { Files.deleteIfExists(temp); }
        try { ReplaySummary.records=0;ReplaySummary.bound(ReplaySummary.MAX_RECORDS+1);throw new AssertionError("budget"); } catch(IllegalArgumentException expected) { }
        System.out.println("ReplaySummaryTest: PASS (synthetic schema, net resources, movement/actions, team slots, incomplete, malformed, gzip truncation, bounds)");
    }
}
