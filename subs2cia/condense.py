from subs2cia.sources import AVSFile
from subs2cia.pickers import picker
from subs2cia.sources import Stream
import subs2cia.subtools as subtools
from subs2cia.sources import common_count
from subs2cia.ffmpeg_tools import export_condensed_audio, export_condensed_video

import logging
from collections import defaultdict
from pathlib import Path


def picked_sources_are_insufficient(d: dict):
    for k in d:
        if d[k] == 'retry':
            return True
    if d['subtitle'] is None:
        return True
    if d['audio'] is None:
        return True
    return False


def insufficient_source_streams(d: dict):
    if len(d['subtitle']) == 0:
        return True
    if len(d['audio']) == 0:
        return True
    return False


class SubCondensed:
    def __init__(self, sources: [AVSFile], outdir: Path, condensed_video: bool, threshold: int, padding: int,
                 partition: int, split: int, demux_overwrite_existing: bool, overwrite_existing_generated: bool,
                 keep_temporaries: bool, target_lang: str, out_audioext: str):
        if len(sources) == 1:  # todo: rework for batching
            outstem = sources[0].filepath.stem
        else:
            outstem = sources[0].filepath.name[0:1+common_count(sources[0].filepath.stem, sources[1].filepath.stem)]

        if outdir is None:
            self.outdir = sources[0].filepath.parent
        else:
            self.outdir = outdir
        self.outstem = outstem
        self.sources = sources
        self.out_audioext = out_audioext

        # logging.debug(f'Will save a file with stem "{self.outstem}" to directory "{self.outdir}"')
        logging.info(f"Mapping input file(s) {sources} to one output file")

        self.partitioned_streams = defaultdict(list)

        self.picked_streams = {
            'audio': None,
            'subtitle': None,
            'video': None
        }

        self.pickers = {
            'audio': None,
            'subtitle': None,
            'video': None
        }

        self.target_lang = target_lang

        self.padding = padding
        self.threshold = threshold
        self.partition = partition
        self.split = split

        self.dialogue_times = None

        self.demux_overwrite_existing = demux_overwrite_existing
        self.overwrite_existing_generated = overwrite_existing_generated
        self.keep_temporaries = keep_temporaries

        self.condensed_video = condensed_video

        self.insufficient = False

    # go through source files and count how many subtitle and audio streams we have
    def get_and_partition_streams(self):
        for sourcefile in self.sources:
            if sourcefile.type == 'video':
                # dig into streams
                for idx, st in enumerate(sourcefile.info['streams']):
                    stype = st['codec_type']
                    self.partitioned_streams[stype].append(Stream(sourcefile, stype, idx))
                continue
            self.partitioned_streams[sourcefile.type].append(Stream(sourcefile, sourcefile.type, None))
            # for stream in sourcefile
        for k in self.partitioned_streams:
            logging.info(f"Found {len(self.partitioned_streams[k])} {k} input streams")
            # logging.debug(f"Streams found: {self.partitioned_streams[k]}")
    def initialize_pickers(self):
        for k in self.pickers:
            self.pickers[k] = picker(self.partitioned_streams[k], target_lang=self.target_lang)

    def choose_streams(self):
        if insufficient_source_streams(self.partitioned_streams):
            logging.error(f"Not enough input sources to generate condensed output for output stem {self.outstem}")
            self.insufficient = True
            return
        while picked_sources_are_insufficient(self.picked_streams):
            for k in self.picked_streams:
                if len(self.partitioned_streams[k]) == 0:
                    logging.debug("no input streams of type {k}")
                    continue
                if self.picked_streams[k] is None:
                    self.picked_streams[k] = next(self.pickers[k])

                # validate picked stream
                if k == 'subtitle':
                    subfile = self.picked_streams[k].demux(overwrite_existing=self.demux_overwrite_existing)  # type AVSFile
                    times = subtools.load_subtitle_times(subfile.filepath)
                    if times is None:
                        self.picked_streams[k] = None

                if k == 'audio':
                    afile = self.picked_streams[k].demux(overwrite_existing=self.demux_overwrite_existing)
                    if afile is None:
                        self.picked_streams[k] = None


                if k == 'video':
                    pass
        logging.info(f"Picked {self.picked_streams['audio']} to use for condensing")
        logging.info(f"Picked {self.picked_streams['video']} to use for condensing")
        logging.info(f"Picked {self.picked_streams['subtitle']} to use for condensing")

    def process_subtitles(self):
        if self.picked_streams['subtitle'] is None:
            logging.error(f'No subtitle stream to process for output stem {self.outstem}')
            return
        if self.insufficient:
            return
        subfile = self.picked_streams['subtitle'].demux(overwrite_existing=self.demux_overwrite_existing)
        times = subtools.load_subtitle_times(subfile.filepath)
        times = subtools.merge_times(times, threshold=self.threshold, padding=self.padding)
        self.dialogue_times = subtools.partition_and_split(sub_times=times, partition_size=1000*self.partition,
                                                           split_size=1000*self.split)

    def export_audio(self):
        if self.picked_streams['audio'] is None:
            logging.error(f'No audio stream to process for output stem {self.outstem}')
            return
        if self.insufficient:
            return
        outfile = self.outdir / (self.outstem + f'.{self.out_audioext}')
        # logging.info(f"exporting condensed audio to {outfile}")  # todo: fix output naming
        if outfile.exists() and not self.overwrite_existing_generated:
            logging.warning(f"Can't write to {outfile}: file exists and not set to overwrite")
            return
        export_condensed_audio(self.dialogue_times, audiofile=self.picked_streams['audio'].get_data_path(),
                               outfile=outfile)

    def export_video(self):
        if self.picked_streams['video'] is None:
            logging.error(f'No video stream to process for output stem {self.outstem}')
            return
        if self.insufficient:
            return
        outfile = self.outdir / (self.outstem + '.mkv')
        logging.info(f"exporting condensed video to {outfile}")
        if outfile.exists() and not self.overwrite_existing_generated:
            logging.warning(f"Can't write to {outfile}: file exists and not set to overwrite")
            return
        export_condensed_video(self.dialogue_times, audiofile=self.picked_streams['audio'].get_data_path(),
                               subfile=self.picked_streams['subtitle'].get_data_path(),
                               videofile=self.picked_streams['video'].get_data_path(),
                               outfile=outfile)
        return

    def export(self):
        subtools.print_compression_ratio(self.dialogue_times, self.picked_streams['audio'].demux_file.filepath)
        if self.condensed_video:
            self.export_video()
        self.export_audio()

    def cleanup(self):
        if self.keep_temporaries:
            return
        for k in ['audio', 'video', 'subtitle']:
            if len(self.partitioned_streams) == 0:
                continue
            for s in self.partitioned_streams[k]:
                s.cleanup_demux()





