import operators
from view import *
from dataset import *
import collections
import parse
import os

# Operators
# These points were found with gimp.
crop_to_dbg = operators.Crop({
    "x_0" : 0,
    "y_0" : 0,
    "x_1" : 280,
    "y_1" : 230
})

# Pos extraction config
crop_to_pos = operators.Crop({
    "x_0" : 0,
    "y_0" : 91,
    "x_1" : 280,
    "y_1" : 100
})
match_pattern = "XYZ: {:f} / {:f} / {:f}"
write_fields = (0, 1, 2)
sink_filename = lambda image_key: image_key[:-4] + "_pos.txt"
extraction_fixes = {"718.png": lambda line: line[1:]}

# Dir extraction config
crop_to_dir = operators.Crop({
    "x_0" : 0,
    "y_0" : 118,
    "x_1" : 280,
    "y_1" : 127
})
# match_pattern = "{} ({:f} / {:f})"
# write_fields = (1, 2)
# sink_filename = lambda image_key: image_key[:] + "_dir.txt"
# extraction_fixes = {}
#

# This curve was designed with gimp.
dbg_binarize = operators.CurvesGi8({
    "x_points": [.88, .90],
    "y_points": [1, 0, 1]
})

gameplay_trim = operators.Trim({
    "start": 474,
    "end": 2008
})

# Views
RAW_DIR = "../memories/raw_data"

LABEL_SOURCE_DIR = "../memories/dbg_text_processed"
VIEW_DIR = "../memories/dbg_position"

OUT_DIR = "../memories/dbg_text_processed"

dbg_processed = View(source_dir = RAW_DIR, \
                     save_dir = VIEW_DIR, \
                     dataset_operators = [gameplay_trim], \
                     image_operators = [crop_to_pos, operators.RgbToG32(), dbg_binarize], \
                     DEBUG = False)

import sys

sys.path.append('/home/william/Workspaces/GameHarness/src/connected_components')
sys.path.append('/home/william/Workspaces/GameHarness/src/labels')

import write
import cv_components
import trigger_loading
import parsing

# run_mode fake enum
REQUEST_ANNOTATIONS = 0
LABEL_DATA = 1
#

MODE = LABEL_DATA

import workflows
w = workflows.Workflow()
COMPOUND_LABEL_PATH = "../src/labels/compound_labels.csv"

triggers = w.S(trigger_loading.LoadTriggers, COMPOUND_LABEL_PATH, LABEL_SOURCE_DIR, name = "Load Triggers")
segments = w.S(cv_components.ConnectedComponents, dbg_processed[:], triggers, name = "Extract Segments")

if MODE == REQUEST_ANNOTATIONS:
    REQUEST_FILE = "request.csv"

    unique_segments_by_size = w.S(cv_components.UniqueSlicePixels, segments, name = "Filter To Unique Segments")

    # Need better namespace isolation here. This "lambda" shouldn't have access to the stages
    # defined above.
    def SegmentsByImage(segments):
        segments_by_image = collections.defaultdict(list)
        for size in segments:
            for segment in segments[size]:
                segments_by_image[segment["image_key"]].append(segment)
        return segments_by_image

    segments_by_image = w.S(SegmentsByImage, unique_segments_by_size, name = "Group Segments by Image")
    w.S(write.WriteSliceLabels, segments_by_image, dataset.LoadFileSizes(LABEL_SOURCE_DIR), REQUEST_FILE, name = "Write Annotation Request")

if MODE == LABEL_DATA:
    LABELED_SEGMENTS_FILE = "labeled_segments.csv"
    raw_annotated_segments = w.S(parsing.LoadAnnotations, LABELED_SEGMENTS_FILE, LABEL_SOURCE_DIR, name = "Load Labeled Segments")
    annotated_segments = w.S(cv_components.InvertAndBinarize, raw_annotated_segments, name = "Convert Loaded Labels")
    labeled_segments = w.S(cv_components.LabelSegments, segments, annotated_segments, name = "Label All Extracted Segments")

    labeled = 0
    unlabeled = 0
    for s in labeled_segments.out:
        if s["name"] == "UNK":
            unlabeled += 1
        else:
            labeled += 1
    print("Labeled: ", labeled)
    print("Unlabeled: ", unlabeled)

    segments_by_image = collections.defaultdict(list)
    for s in labeled_segments.out:
        segments_by_image[s["image_key"]].append(s)

    image_text = w.S(cv_components.SingleLineExtraction, segments_by_image, name = "Extract Lines")
    for image_key in image_text.out:
        write_filename = sink_filename(image_key)
        with open(os.path.join(OUT_DIR, write_filename), "w") as f:
            line = image_text.out[image_key]
            res = parse.parse(match_pattern, line)

            if res is None:
                print("Error during extraction for", image_key, image_text.out[image_key])
                if image_key in extraction_fixes:
                    print("Resolving", image_key)
                    line = extraction_fixes[image_key](line)
                    res = parse.parse(match_pattern, line)

            if res is not None:
                fields = (res[i] for i in write_fields)
                out_string = ", ".join(map(str, fields)) + "\n"
                f.write(out_string)
            else:
                print("Unresolved extraction for", image_key)

# Datastores:
# - Slices
# - Annotations
#
# Steps in a workflow
#
# Gathering Templates:
# - Annotate compound segments
# - Extract compound segments from segment set
# - Name unnamed segments
#
# Using Templates:
# - Extract segments
# - Name the segments using stored templates
# - Do something with the named templates (Line extraction (LTR),
#   whitespace extraction (>1px), and Pattern matching)
#
# An implementation for requesting annotations and using templates would be to:
# - Load annotated compound segments (if any)
# - Create triggers from compound segments (if any)
# - Segment the data
# - Merge segments using triggers
# IF requesting annotations
# - Filter to unique segments
# IF labeling data
# - Load annotated segments and label all segment occurrences
# - Any desired postprocessing
#
# Types of Labels:
#   Compound Segment Creation
#   Segment name annotations
#   Segment False +/- annotations
#   Currently unlabeled segments
#   Error segments
