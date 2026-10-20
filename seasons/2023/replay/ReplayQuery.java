import battlecode.schema.*;
import java.io.*;
import java.nio.*;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

/** Bounded raw-record query. This does not reconstruct world state or bot knowledge. */
public final class ReplayQuery {
    static final String FORMAT = "bcenv.bc2023.replay-query.v1";
    static final String PAGE_FORMAT = "bcenv.bc2023.replay-query.v2";
    static final int MAX_LIMIT = 10000, MAX_ROUND_SPAN = 256;

    static final class Selection {
        final int start, end, limit, offset;
        final boolean paged;
        final Integer robot;
        Selection(int start, int end, Integer robot, int limit) {
            this(start, end, robot, limit, 0, false);
        }
        Selection(int start, int end, Integer robot, int limit, int offset) {
            this(start, end, robot, limit, offset, true);
        }
        private Selection(int start, int end, Integer robot, int limit, int offset, boolean paged) {
            ReplaySummary.require(start >= 0 && end >= start, "round bounds require 0 <= start <= end");
            ReplaySummary.require(robot == null || robot >= 0, "robot ID must be nonnegative");
            ReplaySummary.require(limit >= 1 && limit <= MAX_LIMIT, "record limit must be between 1 and 10000");
            ReplaySummary.require(offset >= 0 && offset <= ReplaySummary.MAX_RECORDS, "record offset out of bounds");
            ReplaySummary.require(!paged || (long)end - start + 1 <= MAX_ROUND_SPAN, "page round span exceeds 256");
            this.start = start; this.end = end; this.robot = robot; this.limit = limit;
            this.offset = offset; this.paged = paged;
        }
        boolean includes(int round, Integer id) {
            // Team deltas have no robot ID and remain explicitly team-scoped.
            return round >= start && round <= end && (id == null || robot == null || robot.equals(id));
        }
    }

    static final class Body {
        final int team, type, observedRound;
        Body(int team, int type, int round) { this.team = team; this.type = type; this.observedRound = round; }
    }

    static final class Collector {
        final Selection selection;
        final List<Object> rows = new ArrayList<Object>();
        final Map<String,Long> counts = new LinkedHashMap<String,Long>();
        long matching;
        Collector(Selection selection) { this.selection = selection; }
        Map<String,Object> row(String kind, int match, int event, int round, int index, Integer robot, Body body) {
            if (!selection.includes(round, robot)) return null;
            matching++;
            Long count = counts.get(kind); counts.put(kind, count == null ? 1L : count + 1);
            if (matching <= selection.offset || rows.size() >= selection.limit) return null;
            Map<String,Object> row = ReplaySummary.obj("kind", kind, "match_index", match,
                "event_index", event, "round", round, "row_index", index);
            if (robot != null) row.putAll(ReplaySummary.obj("robot_id", robot,
                "team_id", body == null ? null : body.team,
                "body_type_code", body == null ? null : body.type,
                "body_type", body == null ? null : BodyType.name(body.type),
                "identity_observed_round", body == null ? null : body.observedRound));
            rows.add(row);
            return row;
        }
        void bodies(SpawnedBodyTable bodies, Map<Integer,Body> identities, int match, int event, int round) {
            if (bodies == null) return;
            for (int i = 0; i < bodies.robotIDsLength(); i++) {
                int id = bodies.robotIDs(i);
                ReplaySummary.require(id >= 0, "negative body ID");
                Body body = new Body(bodies.teamIDs(i), bodies.types(i), round);
                identities.put(id, body);
                Map<String,Object> row = row(round == 0 ? "initial_body" : "spawn", match, event, round, i, id, body);
                if (row != null) row.putAll(ReplaySummary.obj("x", bodies.locs().xs(i), "y", bodies.locs().ys(i)));
            }
        }
    }

    @SuppressWarnings("unchecked")
    static Map<String,Object> extract(byte[] data, Selection selection) {
        // Keep the existing summary contract unchanged. Its full core-array validation
        // occurs before any query result is emitted, including rows beyond the cap.
        Map<String,Object> summary = ReplaySummary.extract(data);
        GameWrapper game = GameWrapper.getRootAsGameWrapper(ByteBuffer.wrap(data));
        Collector out = new Collector(selection);
        Map<Integer,Body> identities = new HashMap<Integer,Body>();
        List<Object> matches = new ArrayList<Object>();
        List<Object> summaries = (List<Object>) summary.get("matches");
        Map<String,Object> coverage = null;
        int match = -1, selectedRounds = 0;
        for (int eventIndex = 0; eventIndex < game.eventsLength(); eventIndex++) {
            EventWrapper event = game.events(eventIndex);
            if (event.eType() == Event.MatchHeader) {
                match++; identities.clear(); selectedRounds = 0;
                MatchHeader header = (MatchHeader) event.e(new MatchHeader());
                Map<String,Object> source = (Map<String,Object>) summaries.get(match);
                coverage = ReplaySummary.obj("match_index", match, "map", source.get("map"),
                    "observed_round_events", source.get("round_events"), "last_observed_round", source.get("last_round_id"),
                    "reported_total_rounds", source.get("total_rounds"), "match_footer_present", source.get("match_footer_present"),
                    "source_round_ids_contiguous", source.get("round_ids_contiguous"),
                    "source_round_records_cover_reported_rounds", source.get("round_records_cover_reported_rounds"),
                    "source_resource_records_cover_observed_rounds", source.get("resource_records_cover_observed_rounds"),
                    "selected_round_events", 0, "first_selected_round", null, "last_selected_round", null,
                    "all_requested_positive_rounds_present", selection.end == 0);
                matches.add(coverage);
                out.bodies(header.map().bodies(), identities, match, eventIndex, 0);
            } else if (event.eType() == Event.Round) {
                Round r = (Round) event.e(new Round());
                int round = r.roundID();
                if (round >= selection.start && round <= selection.end) {
                    selectedRounds++;
                    coverage.put("selected_round_events", selectedRounds);
                    if (coverage.get("first_selected_round") == null) coverage.put("first_selected_round", round);
                    coverage.put("last_selected_round", round);
                    long requested = (long) selection.end - Math.max(1, selection.start) + 1;
                    coverage.put("all_requested_positive_rounds_present", selectedRounds == requested);
                }
                // These are schema groups, not a cross-group temporal ordering.
                out.bodies(r.spawnedBodies(), identities, match, eventIndex, round);
                for (int i = 0; i < r.teamIDsLength(); i++) {
                    Map<String,Object> row = out.row("team_resource_delta", match, eventIndex, round, i, null, null);
                    if (row != null) row.putAll(ReplaySummary.obj("team_id", r.teamIDs(i),
                        "adamantium_delta", r.teamAdChanges(i), "mana_delta", r.teamMnChanges(i), "elixir_delta", r.teamExChanges(i)));
                }
                for (int i = 0; i < r.movedIDsLength(); i++) {
                    int id = r.movedIDs(i); ReplaySummary.require(id >= 0, "negative movement body ID");
                    Map<String,Object> row = out.row("movement", match, eventIndex, round, i, id, identities.get(id));
                    if (row != null) row.putAll(ReplaySummary.obj("x", r.movedLocs().xs(i), "y", r.movedLocs().ys(i)));
                }
                for (int i = 0; i < r.actionIDsLength(); i++) {
                    int id = r.actionIDs(i); ReplaySummary.require(id >= 0, "negative action body ID");
                    Map<String,Object> row = out.row("action", match, eventIndex, round, i, id, identities.get(id));
                    if (row != null) row.putAll(ReplaySummary.obj("action_code", r.actions(i), "action", Action.name(r.actions(i)), "raw_target", r.actionTargets(i)));
                }
                for (int i = 0; i < r.diedIDsLength(); i++) {
                    int id = r.diedIDs(i); ReplaySummary.require(id >= 0, "negative death body ID");
                    out.row("death", match, eventIndex, round, i, id, identities.get(id));
                    identities.remove(id);
                }
            }
        }
        // The complete source was parsed and validated before checking actual EOF.
        ReplaySummary.require(selection.offset <= out.matching, "record offset exceeds matching count");
        boolean truncated = out.matching > out.rows.size();
        Map<String,Object> result = ReplaySummary.obj("format", selection.paged ? PAGE_FORMAT : FORMAT, "status", summary.get("status"),
            "spec_version", summary.get("spec_version"), "extracted_fields_valid", true,
            "all_schema_fields_validated", false, "source_stream_complete", "complete".equals(summary.get("status")),
            "query", ReplaySummary.obj("round_start", selection.start, "round_end", selection.end,
                "round_bounds_inclusive", true, "robot_id", selection.robot, "record_limit", selection.limit,
                "team_resource_rows_included_with_robot_filter", true, "match_scope", "all matches, separately indexed"),
            "matching_record_count", out.matching, "matching_counts_by_kind", out.counts,
            "returned_record_count", out.rows.size(), "truncated", truncated,
            "selected_records_complete", !truncated, "matches", matches, "records", out.rows,
            "limits", ReplaySummary.obj("max_compressed_bytes", ReplaySummary.MAX_BYTES,
                "max_uncompressed_bytes", ReplaySummary.MAX_BYTES, "max_events", ReplaySummary.MAX_EVENTS,
                "max_matches", ReplaySummary.MAX_MATCHES, "max_core_records", ReplaySummary.MAX_RECORDS,
                "max_returned_records", MAX_LIMIT),
            "semantics", Arrays.asList(
                "Records are official replay arrays, not reconstructed world state or any robot's visibility or knowledge.",
                "Initial map bodies are initial_body records at synthetic round 0; spawned bodies use their recorded round.",
                "Record order is match/event order, then spawn, team delta, movement, action, death groups and vector row order. It does not establish within-round chronology across groups.",
                "Identity/type fields join initial/spawn records by ID within the match; unknown identity is null. Death removes the identity after that round's groups. No death cause or position is inferred.",
                "Movement coordinates are recorded new locations; intent and movement cause, including currents, are not inferred.",
                "Action codes/names and raw_target are retained exactly. Targets are not decoded; aesthetic events do not establish hits, damage, or resource collection.",
                "Team resource deltas are net team changes, not gross collection, spending, balances, or robot-local resources. Both teams' rows remain when filtering a robot.",
                "The record cap retains the first matching records in documented presentation order; matching counts cover the entire validated stream, even after truncation.",
                "selected_records_complete means all matching records present in the source were returned. It does not assert the requested rounds exist or the source stream is complete; inspect match coverage and source_stream_complete.",
                "Only extracted core arrays and event ordering are validated; terrain, indicators, bytecodes, wells, islands, and other schema fields are not validated."));
        if (selection.paged) {
            Map<String,Object> query = (Map<String,Object>)result.get("query");
            query.put("record_offset", selection.offset);
            long end = (long)selection.offset + out.rows.size();
            result.put("page_records_complete", true);
            result.put("page_end_offset", end);
            result.put("next_record_offset", end < out.matching ? end : null);
            ((Map<String,Object>)result.get("limits")).put("max_round_span", MAX_ROUND_SPAN);
            ((Map<String,Object>)result.get("limits")).put("max_record_offset", ReplaySummary.MAX_RECORDS);
            List<String> semantics = new ArrayList<String>((List<String>)result.get("semantics"));
            semantics.set(7, "The page is the exact bounded slice at record_offset in matching presentation order. Counts and match coverage span the entire validated stream, including before/after the page.");
            semantics.add("page_records_complete means the requested bounded source slice is present; selected_records_complete means this single page contains every matching row in the window. Neither asserts requested rounds exist.");
            result.put("semantics", semantics);
        }
        return result;
    }

    static byte[] boundedRead(InputStream in, String kind) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192]; int n, total = 0;
        while ((n = in.read(buffer)) != -1) {
            total += n;
            ReplaySummary.require(total <= ReplaySummary.MAX_BYTES, kind + " input limit exceeded");
            out.write(buffer, 0, n);
        }
        return out.toByteArray();
    }

    static String sha256(byte[] bytes) {
        try {
            StringBuilder hex = new StringBuilder();
            for (byte b : MessageDigest.getInstance("SHA-256").digest(bytes)) hex.append(String.format("%02x", b & 255));
            return hex.toString();
        } catch (NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }

    static Map<String,Object> query(Path path, Selection selection) throws IOException {
        ReplaySummary.require(Files.isRegularFile(path), "replay must be a regular file");
        ReplaySummary.require(Files.size(path) <= ReplaySummary.MAX_BYTES, "compressed input limit exceeded");
        byte[] compressed;
        try (InputStream in = Files.newInputStream(path)) { compressed = boundedRead(in, "compressed"); }
        byte[] data;
        try (InputStream in = new GZIPInputStream(new ByteArrayInputStream(compressed))) { data = boundedRead(in, "uncompressed"); }
        Map<String,Object> result = extract(data, selection);
        // The hash covers exactly the compressed snapshot that was parsed, not a second file read.
        result.put("source_replay", ReplaySummary.obj("path", path.toAbsolutePath().normalize().toString(),
            "sha256", sha256(compressed), "compressed_bytes", compressed.length, "uncompressed_bytes", data.length));
        return result;
    }

    public static void main(String[] args) {
        int exit = 0; Map<String,Object> result;
        boolean paged = args.length > 0 && "--page".equals(args[0]);
        try {
            ReplaySummary.require(paged ? args.length == 7 : args.length == 5,
                "usage: ReplayQuery [--page] replay.bc23 round-start round-end robot-id-or-all limit [record-offset]");
            int base = paged ? 1 : 0;
            int start = Integer.parseInt(args[base + 1]), end = Integer.parseInt(args[base + 2]);
            Integer robot = "all".equals(args[base + 3]) ? null : Integer.valueOf(args[base + 3]);
            int limit = Integer.parseInt(args[base + 4]);
            Selection selection = paged ? new Selection(start, end, robot, limit, Integer.parseInt(args[6]))
                : new Selection(start, end, robot, limit);
            result = query(Paths.get(args[base]), selection);
            if (!"complete".equals(result.get("status"))) exit = 2;
        } catch (IOException | RuntimeException e) {
            result = ReplaySummary.obj("format", paged ? PAGE_FORMAT : FORMAT, "status", "invalid", "extracted_fields_valid", false,
                "error", e.getClass().getSimpleName() + ": " + e.getMessage()); exit = 2;
        }
        System.out.println(ReplaySummary.json(result));
        if (exit != 0) System.exit(exit);
    }
}
