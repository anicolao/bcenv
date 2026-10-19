import battlecode.schema.*;
import battlecode.common.GameConstants;
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

/** Bounded evidence extractor for the pinned official BC2023 replay schema. */
public final class ReplaySummary {
    static final int MAX_BYTES = 64 * 1024 * 1024;
    static final int MAX_EVENTS = 100000, MAX_MATCHES = 100, MAX_RECORDS = 5000000, SAMPLE_LIMIT = 32;
    static long records;
    static Map<String,Object> obj(Object... pairs) {
        Map<String,Object> m = new LinkedHashMap<String,Object>();
        for (int i=0;i<pairs.length;i+=2) m.put((String)pairs[i],pairs[i+1]);
        return m;
    }
    static void require(boolean ok, String why) { if (!ok) throw new IllegalArgumentException(why); }
    static int bound(int n) { require(n>=0 && n<=MAX_RECORDS,"invalid or excessive vector length"); records+=n; require(records<=MAX_RECORDS,"record budget exceeded"); return n; }
    static String string(String s) { require(s==null || s.length()<=4096,"metadata string too long"); return s; }
    static void equal(int n, int... lengths) { for(int v:lengths) require(n==v,"parallel vector length mismatch"); }
    static void sample(List<Object> samples,Object value) { if(samples.size()<SAMPLE_LIMIT) samples.add(value); }
    static String slot(int id) { return id==1 ? "A" : id==2 ? "B" : id==0 ? "NEUTRAL" : "UNKNOWN"; }
    static void winner(int id) { require(id==1 || id==2,"invalid winner team ID"); }
    static final class Match {
        Map<String,Object> out = obj();
        Map<Integer,Integer> robotTeams = new HashMap<Integer,Integer>();
        Map<String,Long> actionCounts = new TreeMap<String,Long>();
        Map<String,long[]> resources = new LinkedHashMap<String,long[]>();
        List<Object> actions = new ArrayList<Object>(), moves = new ArrayList<Object>(), resourceSamples = new ArrayList<Object>();
        long movementCount, deathCount, spawnCount, actionCount, resourceRows;
        int rounds,lastRound; boolean contiguous=true,footer=false,resourcesEveryRound=true;
        Match(MatchHeader h,int index) {
            GameMap map=h.map(); require(map!=null,"missing map");
            out.putAll(obj("index",index,"map",string(map.name()),"max_rounds",h.maxRounds()));
            bodies(map.bodies(),true);
        }
        int bodies(SpawnedBodyTable b,boolean initial) {
            if(b==null) return 0;
            int n=bound(b.robotIDsLength()); equal(n,b.teamIDsLength(),b.typesLength());
            VecTable loc=b.locs(); require(n==0 || loc!=null,"missing body locations");
            if(loc!=null) equal(n,loc.xsLength(),loc.ysLength());
            for(int i=0;i<n;i++) {
                int id=b.robotIDs(i),team=b.teamIDs(i),type=b.types(i);
                require(team==1 || team==2,"invalid body team ID");
                require(type>=0 && type<BodyType.names.length,"invalid body type");
                require(!robotTeams.containsKey(id),"duplicate live body ID");
                require(robotTeams.size()<100000,"live body budget exceeded");
                robotTeams.put(id,team); loc.xs(i);loc.ys(i);
            }
            if(initial) out.put("initial_body_count",n); else spawnCount+=n;
            return n;
        }
        void round(Round r) {
            int round=r.roundID(); require(round>lastRound,"non-increasing round ID");
            if(round!=lastRound+1) contiguous=false;
            rounds++;lastRound=round;
            bodies(r.spawnedBodies(),false);
            int n=bound(r.teamIDsLength()); equal(n,r.teamAdChangesLength(),r.teamMnChangesLength(),r.teamExChangesLength());
            if(n!=2) resourcesEveryRound=false;
            Set<Integer> seen=new HashSet<Integer>();
            for(int i=0;i<n;i++) {
                int team=r.teamIDs(i); require((team==1 || team==2) && seen.add(team),"invalid or duplicate resource team ID");
                String key=slot(team); long[] sums=resources.get(key); if(sums==null) { sums=new long[3]; resources.put(key,sums); }
                int a=r.teamAdChanges(i),m=r.teamMnChanges(i),e=r.teamExChanges(i);
                sums[0]+=a;sums[1]+=m;sums[2]+=e;resourceRows++;
                sample(resourceSamples,obj("round",round,"team_id",team,"adamantium_delta",a,"mana_delta",m,"elixir_delta",e));
            }
            n=bound(r.movedIDsLength()); VecTable loc=r.movedLocs(); require(n==0 || loc!=null,"missing movement locations");
            if(loc!=null) equal(n,loc.xsLength(),loc.ysLength());
            for(int i=0;i<n;i++) { int id=r.movedIDs(i); sample(moves,obj("round",round,"robot_id",id,"team_id",robotTeams.get(id),"x",loc.xs(i),"y",loc.ys(i))); }
            movementCount+=n;
            n=bound(r.actionIDsLength());equal(n,r.actionsLength(),r.actionTargetsLength());
            for(int i=0;i<n;i++) {
                int code=r.actions(i);require(code>=0 && code<Action.names.length,"unknown action code");
                String name=Action.name(code); Long count=actionCounts.get(name);actionCounts.put(name,count==null?1:count+1);
                int id=r.actionIDs(i),target=r.actionTargets(i);
                sample(actions,obj("round",round,"robot_id",id,"team_id",robotTeams.get(id),"action",name,"raw_target",target));
            }
            actionCount+=n;n=bound(r.diedIDsLength());
            for(int i=0;i<n;i++) robotTeams.remove(r.diedIDs(i)); deathCount+=n;
        }
        Map<String,Object> finish() {
            Map<String,Object> totals=obj();for(Map.Entry<String,long[]> e:resources.entrySet()) { long[] v=e.getValue();totals.put(e.getKey(),obj("adamantium",v[0],"mana",v[1],"elixir",v[2])); }
            out.putAll(obj("round_events",rounds,"last_round_id",lastRound,"match_footer_present",footer,
                "round_ids_contiguous",contiguous,
                "round_records_cover_reported_rounds",footer && contiguous && Integer.valueOf(lastRound).equals(out.get("total_rounds")),
                "resource_records_cover_observed_rounds",resourcesEveryRound,"movement_event_count",movementCount,"spawned_body_count",spawnCount,
                "death_event_count",deathCount,"action_event_count",actionCount,"action_counts",actionCounts,
                "team_resource_net_deltas",totals,"team_resource_rows",resourceRows,
                "movement_samples",moves,"action_samples",actions,"resource_delta_samples",resourceSamples,
                "samples_complete",movementCount<=SAMPLE_LIMIT && actionCount<=SAMPLE_LIMIT && resourceRows<=SAMPLE_LIMIT));
            return out;
        }
    }
    static Map<String,Object> extract(byte[] data) {
        records=0;
        GameWrapper game=GameWrapper.getRootAsGameWrapper(ByteBuffer.wrap(data));
        int count=game.eventsLength();require(count>=2 && count<=MAX_EVENTS,"invalid or excessive event count");
        List<Object> teams=new ArrayList<Object>(),matches=new ArrayList<Object>();
        List<Integer> headers=new ArrayList<Integer>(),footers=new ArrayList<Integer>();
        Match current=null; boolean header=false,footer=false; Integer gameWinner=null;String spec=null;
        for(int i=0;i<count;i++) {
            EventWrapper event=game.events(i);require(event!=null,"missing event");
            switch(event.eType()) {
            case Event.GameHeader: {
                require(i==0 && !header,"unexpected game header");header=true;
                GameHeader h=(GameHeader)event.e(new GameHeader());require(h!=null,"missing game header payload");
                spec=string(h.specVersion());require(GameConstants.SPEC_VERSION.equals(spec),"unsupported replay spec version for pinned engine " + GameConstants.SPEC_VERSION);
                require(h.teamsLength()==2,"expected two teams");
                Set<Integer> ids=new HashSet<Integer>();
                for(int j=0;j<2;j++) { TeamData t=h.teams(j); int id=t.teamID(); require((id==1 || id==2)&&ids.add(id),"invalid team identities"); teams.add(obj("slot",slot(id),"team_id",id,"name",string(t.name()),"package",string(t.packageName()))); }
                break;
            }
            case Event.MatchHeader: {
                require(header && !footer && current==null,"unexpected match header");require(headers.size()<MAX_MATCHES,"match budget exceeded");
                current=new Match((MatchHeader)event.e(new MatchHeader()),headers.size()); headers.add(i);break;
            }
            case Event.Round:
                require(current!=null && !footer,"round outside match"); current.round((Round)event.e(new Round()));break;
            case Event.MatchFooter: {
                require(current!=null && !footer,"unexpected match footer");MatchFooter f=(MatchFooter)event.e(new MatchFooter());winner(f.winner());
                require(f.totalRounds()>=current.lastRound,"footer rounds precede observed rounds");
                current.footer=true;current.out.putAll(obj("winner_team_id",(int)f.winner(),"winner_slot",slot(f.winner()),"total_rounds",f.totalRounds()));
                matches.add(current.finish());current=null;footers.add(i);break;
            }
            case Event.GameFooter: {
                require(header && !footer && current==null && i==count-1,"unexpected game footer");footer=true;
                GameFooter f=(GameFooter)event.e(new GameFooter());winner(f.winner());gameWinner=(int)f.winner();break;
            }
            default: throw new IllegalArgumentException("unknown event type");
            }
        }
        if(current!=null) matches.add(current.finish());
        require(matches.size()>0,"no matches");
        equal(headers.size(),game.matchHeadersLength());equal(footers.size(),game.matchFootersLength());
        for(int i=0;i<headers.size();i++) require(game.matchHeaders(i)==headers.get(i),"match header index mismatch");
        for(int i=0;i<footers.size();i++) require(game.matchFooters(i)==footers.get(i),"match footer index mismatch");
        return obj("format","bcenv.bc2023.replay-summary.v1","status",footer?"complete":"incomplete","spec_version",spec,
            "game_footer_present",footer,"winner_team_id",gameWinner,"winner_slot",gameWinner==null?null:slot(gameWinner),
            "event_count",count,"teams",teams,"matches",matches,"extracted_fields_valid",true,"all_schema_fields_validated",false,
            "limits",obj("max_uncompressed_bytes",MAX_BYTES,"max_compressed_bytes",MAX_BYTES,"max_events",MAX_EVENTS,"max_records",MAX_RECORDS,"samples_per_category_per_match",SAMPLE_LIMIT),
            "limitations",Arrays.asList("Resource deltas are net recorded team changes, not gross mining, spending, or efficiency.",
                "Movement events record locations, not pathfinding quality, intent, or movement cause; currents may contribute.",
                "Action targets retain official raw encoding; counts are events, not successful hits or damage totals.",
                "Only extracted fields and event ordering are checked; this is not a full FlatBuffers verifier.",
                "No strategy quality, combat effectiveness, visibility, or unrecorded telemetry is inferred."));
    }
    static byte[] read(Path path) throws IOException {
        require(Files.size(path)<=MAX_BYTES,"compressed input limit exceeded");
        try(InputStream in=new GZIPInputStream(Files.newInputStream(path));ByteArrayOutputStream out=new ByteArrayOutputStream()) {
            byte[] buffer=new byte[8192];int n,total=0;
            while((n=in.read(buffer))!=-1) { total+=n; require(total<=MAX_BYTES,"uncompressed input limit exceeded");out.write(buffer,0,n); }
            return out.toByteArray();
        }
    }
    static String json(Object value) {
        if(value==null) return "null";
        if(value instanceof Number || value instanceof Boolean) return value.toString();
        StringBuilder b=new StringBuilder();
        if(value instanceof Map) { b.append('{');boolean first=true;for(Object entry:((Map<?,?>)value).entrySet()) { Map.Entry<?,?> e=(Map.Entry<?,?>)entry;if(!first)b.append(',');first=false;b.append(json(e.getKey())).append(':').append(json(e.getValue())); } return b.append('}').toString(); }
        if(value instanceof Iterable) { b.append('[');boolean first=true;for(Object v:(Iterable<?>)value) { if(!first)b.append(',');first=false;b.append(json(v)); } return b.append(']').toString(); }
        b.append('"');for(char c:value.toString().toCharArray()) { switch(c) { case '"':b.append("\\\"");break;case '\\':b.append("\\\\");break;case '\n':b.append("\\n");break;case '\r':b.append("\\r");break;case '\t':b.append("\\t");break;default:if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c); } }return b.append('"').toString();
    }
    public static void main(String[] args) {
        int exit=0;Map<String,Object> result;
        try { require(args.length==1,"usage: ReplaySummary replay.bc23");result=extract(read(Paths.get(args[0])));if(!"complete".equals(result.get("status")))exit=2; }
        catch(IOException | RuntimeException e) { result=obj("format","bcenv.bc2023.replay-summary.v1","status","invalid","extracted_fields_valid",false,"error",e.getClass().getSimpleName()+": "+e.getMessage());exit=2; }
        System.out.println(json(result));if(exit!=0)System.exit(exit);
    }
}
