import battlecode.schema.*;
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

/** Own-team recorded turn diagnostics, never stdout or reconstructed robot state. */
public final class OwnDiagnostics {
    static final String FORMAT = "bcenv.bc2023.own-diagnostics-query.v1";

    static void bodies(SpawnedBodyTable table, Map<Integer,Integer> live, Set<Integer> seen) {
        if (table == null) return;
        for (int i = 0; i < table.robotIDsLength(); i++) {
            int id = table.robotIDs(i), team = table.teamIDs(i);
            ReplaySummary.require(id >= 0 && (team == 1 || team == 2), "invalid body identity");
            ReplaySummary.require(seen.add(id), "duplicate or reused body identity");
            ReplaySummary.require(seen.size() <= ReplaySummary.MAX_RECORDS, "identity budget exceeded");
            live.put(id, team);
        }
    }

    static boolean validString(String value) {
        if (value == null || value.length() > 64) return false;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i >= value.length() || !Character.isLowSurrogate(value.charAt(i))) return false;
            } else if (Character.isLowSurrogate(c)) return false;
        }
        return true;
    }

    static void known(int id, Map<Integer,Integer> live) {
        ReplaySummary.require(id >= 0 && live.containsKey(id), "unknown diagnostic body identity");
    }

    static Map<String,Object> extract(byte[] data, int ownTeam, ReplayQuery.Selection selection) {
        ReplaySummary.require(ownTeam == 1 || ownTeam == 2, "invalid own team");
        ReplaySummary.require(selection != null && selection.paged, "paged selection required");
        ReplaySummary.require(data != null && data.length <= ReplaySummary.MAX_BYTES, "uncompressed input limit exceeded");
        Map<String,Object> summary = ReplaySummary.extract(data);
        GameWrapper game = GameWrapper.getRootAsGameWrapper(ByteBuffer.wrap(data));
        Map<Integer,Integer> live = new HashMap<Integer,Integer>();
        Set<Integer> seen = new HashSet<Integer>();
        List<Object> rows = new ArrayList<Object>();
        int match = -1;
        long matching = 0;
        for (int eventIndex = 0; eventIndex < game.eventsLength(); eventIndex++) {
            EventWrapper event = game.events(eventIndex);
            if (event.eType() == Event.MatchHeader) {
                match++; live.clear(); seen.clear();
                MatchHeader header = (MatchHeader) event.e(new MatchHeader());
                bodies(header.map().bodies(), live, seen);
            } else if (event.eType() == Event.Round) {
                Round round = (Round) event.e(new Round());
                bodies(round.spawnedBodies(), live, seen);
                int ni = ReplaySummary.bound(round.indicatorStringIDsLength());
                ReplaySummary.equal(ni, round.indicatorStringsLength());
                int nb = ReplaySummary.bound(round.bytecodeIDsLength());
                ReplaySummary.equal(nb, round.bytecodesUsedLength());
                Map<Integer,String> strings = new HashMap<Integer,String>();
                Map<Integer,Integer> bytecodes = new HashMap<Integer,Integer>();
                SortedSet<Integer> ids = new TreeSet<Integer>();
                for (int i = 0; i < ni; i++) {
                    int id = round.indicatorStringIDs(i); known(id, live);
                    String value = round.indicatorStrings(i);
                    ReplaySummary.require(validString(value), "invalid indicator string");
                    ReplaySummary.require(!strings.containsKey(id), "duplicate indicator identity");
                    strings.put(id, value); ids.add(id);
                }
                for (int i = 0; i < nb; i++) {
                    int id = round.bytecodeIDs(i), value = round.bytecodesUsed(i); known(id, live);
                    ReplaySummary.require(value >= 0, "negative bytecode usage");
                    ReplaySummary.require(!bytecodes.containsKey(id), "duplicate bytecode identity");
                    bytecodes.put(id, value); ids.add(id);
                }
                for (int id : ids) {
                    // Ownership is enforced before counts and pagination. Neither
                    // omitted opponent rows nor their vector positions are exposed.
                    if (live.get(id) != ownTeam || !selection.includes(round.roundID(), id)) continue;
                    matching++;
                    if (matching <= selection.offset || rows.size() >= selection.limit) continue;
                    rows.add(ReplaySummary.obj("match_index", match, "round", round.roundID(),
                        "robot_id", id, "indicator_string", strings.get(id), "bytecodes_used", bytecodes.get(id)));
                }
                // Same-round dying robots still own their diagnostic records.
                for (int i = 0; i < round.diedIDsLength(); i++) {
                    int id = round.diedIDs(i); known(id, live); live.remove(id);
                }
            }
        }
        ReplaySummary.require(selection.offset <= matching, "record offset exceeds matching count");
        long end = (long)selection.offset + rows.size();
        return ReplaySummary.obj("format", FORMAT, "spec_version", summary.get("spec_version"),
            "status", summary.get("status"), "source_stream_complete", "complete".equals(summary.get("status")),
            "extracted_fields_valid", true, "all_schema_fields_validated", false,
            "page_records_complete", true, "truncated", rows.size() < matching,
            "selected_records_complete", rows.size() == matching,
            "query", ReplaySummary.obj("round_start", selection.start, "round_end", selection.end,
                "robot_id", selection.robot, "record_limit", selection.limit, "record_offset", selection.offset,
                "own_team_id", ownTeam), "records", rows, "matching_record_count", matching,
            "returned_record_count", rows.size(), "page_end_offset", end,
            "next_record_offset", end < matching ? end : null);
    }

    static Map<String,Object> query(Path path, int ownTeam, ReplayQuery.Selection selection) throws IOException {
        ReplaySummary.require(Files.isRegularFile(path), "replay must be a regular file");
        ReplaySummary.require(Files.size(path) <= ReplaySummary.MAX_BYTES, "compressed input limit exceeded");
        byte[] compressed, data;
        try (InputStream in = Files.newInputStream(path)) { compressed = ReplayQuery.boundedRead(in, "compressed"); }
        try (InputStream in = new GZIPInputStream(new ByteArrayInputStream(compressed))) {
            data = ReplayQuery.boundedRead(in, "uncompressed");
        }
        Map<String,Object> result = extract(data, ownTeam, selection);
        result.put("source_replay", ReplaySummary.obj("sha256", ReplayQuery.sha256(compressed),
            "compressed_bytes", compressed.length, "uncompressed_bytes", data.length));
        return result;
    }

    public static void main(String[] args) {
        int exit = 0; Map<String,Object> result;
        try {
            ReplaySummary.require(args.length == 7,
                "usage: OwnDiagnostics PATH START END ROBOT(-1) LIMIT OFFSET OWNTEAM");
            int robot = Integer.parseInt(args[3]);
            ReplaySummary.require(robot >= -1, "invalid robot selection");
            ReplayQuery.Selection selection = new ReplayQuery.Selection(Integer.parseInt(args[1]),
                Integer.parseInt(args[2]), robot == -1 ? null : robot,
                Integer.parseInt(args[4]), Integer.parseInt(args[5]));
            result = query(Paths.get(args[0]), Integer.parseInt(args[6]), selection);
            if (!"complete".equals(result.get("status"))) exit = 2;
        } catch (IOException | RuntimeException e) {
            result = ReplaySummary.obj("format", FORMAT, "status", "invalid", "extracted_fields_valid", false,
                "error", e.getClass().getSimpleName() + ": " + e.getMessage()); exit = 2;
        }
        System.out.println(ReplaySummary.json(result));
        if (exit != 0) System.exit(exit);
    }
}
