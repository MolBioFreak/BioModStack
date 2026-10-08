import React, { useEffect, useRef, useState, useMemo, useCallback } from "react";
import {
  AdvancedOptions,
  CheckboxField,
  DropdownButton,
  generateField,
  RadioGroupField,
  InputField
} from "@teselagen/ui";
import {
  filterSequenceString,
  getReverseComplementSequenceString,
  calculatePercentGC,
  calculateEndStability,
  findSequenceMatches
} from "@teselagen/sequence-utils";

import AddOrEditAnnotationDialog from "../AddOrEditAnnotationDialog";
import { convertRangeTo0Based } from "@teselagen/range-utils";
import classNames from "classnames";
import "./style.css";
import { getSequenceWithinRange } from "@teselagen/range-utils";
import { flatMap } from "lodash-es";
import CaretPositioning, {
  selectionSaveCaretPosition
} from "./EditCaretPosition";
import { Menu, MenuItem, Callout, Intent, Spinner, Tag } from "@blueprintjs/core";

import MeltingTemp from "../../StatusBar/MeltingTemp";
import { getStructuredBases } from "../../RowItem/StackedAnnotations/getStructuredBases";

/**
 * PrimerSequenceInput - A sequence input field that auto-detects binding sites
 */
const PrimerSequenceInput = generateField(function PrimerSequenceInput({
  input,
  disabled,
  sequenceData,
  isCircular,
  onBindingSiteFound,
  change
}) {
  const [searchResults, setSearchResults] = useState([]);
  const [isSearching, setIsSearching] = useState(false);
  const [selectedMatch, setSelectedMatch] = useState(0);
  const sequence = input.value || "";

  // Debounced search for binding sites
  useEffect(() => {
    if (!sequence || sequence.length < 6) {
      setSearchResults([]);
      return;
    }

    setIsSearching(true);
    const timer = setTimeout(() => {
      try {
        // Search forward strand
        const forwardMatches = findSequenceMatches(
          sequenceData.sequence,
          sequence,
          {
            isCircular,
            isAmbiguous: true, // Allow ambiguous bases like N, R, Y etc
            searchReverseStrand: false
          }
        );

        // Search reverse complement
        const reverseSeq = getReverseComplementSequenceString(sequence);
        const reverseMatches = findSequenceMatches(
          sequenceData.sequence,
          reverseSeq,
          {
            isCircular,
            isAmbiguous: true,
            searchReverseStrand: false
          }
        ).map(m => ({ ...m, isReverse: true }));

        const allMatches = [
          ...forwardMatches.map(m => ({ ...m, isReverse: false })),
          ...reverseMatches
        ].sort((a, b) => a.start - b.start);

        setSearchResults(allMatches);

        // Auto-select first match and update form
        if (allMatches.length > 0) {
          const match = allMatches[0];
          onBindingSiteFound(match);
          setSelectedMatch(0);
        }
      } catch (e) {
        console.error("Primer search error:", e);
        setSearchResults([]);
      }
      setIsSearching(false);
    }, 300);

    return () => clearTimeout(timer);
  }, [sequence, sequenceData.sequence, isCircular, onBindingSiteFound]);

  const handleMatchSelect = useCallback((index) => {
    setSelectedMatch(index);
    if (searchResults[index]) {
      onBindingSiteFound(searchResults[index]);
    }
  }, [searchResults, onBindingSiteFound]);

  return (
    <div className="primer-sequence-input">
      <div className="bp3-form-group bp3-inline">
        <label className="bp3-label" style={{ marginRight: 10 }}>
          Primer Sequence
          <span className="bp3-text-muted" style={{ marginLeft: 8, fontSize: 11 }}>
            (5' → 3')
          </span>
        </label>
        <input
          type="text"
          className="bp3-input"
          style={{
            fontFamily: "monospace",
            textTransform: "uppercase",
            minWidth: 280
          }}
          placeholder="Enter primer sequence (e.g., ATGCATGCATGC)"
          value={sequence}
          onChange={(e) => {
            const [filtered] = filterSequenceString(e.target.value, sequenceData);
            input.onChange(filtered.toUpperCase());
          }}
          disabled={disabled}
        />
      </div>

      {/* Results indicator */}
      <div style={{ marginTop: 8, marginBottom: 8 }}>
        {isSearching ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Spinner size={14} />
            <span className="bp3-text-muted">Searching for binding sites...</span>
          </div>
        ) : sequence.length >= 6 ? (
          searchResults.length > 0 ? (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <Tag intent={Intent.SUCCESS} minimal>
                  {searchResults.length} binding site{searchResults.length > 1 ? "s" : ""} found
                </Tag>
              </div>
              {searchResults.length > 1 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {searchResults.slice(0, 10).map((match, idx) => (
                    <Tag
                      key={idx}
                      interactive
                      intent={idx === selectedMatch ? Intent.PRIMARY : Intent.NONE}
                      onClick={() => handleMatchSelect(idx)}
                      style={{ cursor: "pointer" }}
                    >
                      {match.start + 1}-{match.end + 1}
                      {match.isReverse ? " (Rev)" : ""}
                    </Tag>
                  ))}
                  {searchResults.length > 10 && (
                    <span className="bp3-text-muted">+{searchResults.length - 10} more</span>
                  )}
                </div>
              )}
            </div>
          ) : (
            <Callout intent={Intent.WARNING} icon="warning-sign" style={{ padding: "6px 10px" }}>
              No exact binding sites found. Check your sequence or try a shorter primer.
            </Callout>
          )
        ) : sequence.length > 0 ? (
          <span className="bp3-text-muted" style={{ fontSize: 12 }}>
            Enter at least 6 bases to search for binding sites
          </span>
        ) : null}
      </div>
    </div>
  );
});

const CustomContentEditable = generateField(function CustomContentEditable({
  input,
  disabled,
  sequenceData,
  sequenceLength,
  start,
  end,
  primerBindsOn,
  forward
}) {
  const bases = input.value;
  const [hasTempError, setTempError] = useState(false);
  const inputRef = useRef(null);
  const [caretPosition, setCaretPosition] = useState({ start: 0, end: 0 });

  const emitChange = e => {
    const newVal = e.target.innerText;
    const savedCaretPosition = CaretPositioning.saveSelection(e.currentTarget);
    setCaretPosition(savedCaretPosition);
    const [newBases, warnings] = filterSequenceString(newVal, sequenceData);

    if (warnings.length) {
      setTempError(true);
      setTimeout(() => {
        setTempError(false);
      }, 200);
    }
    const restore = selectionSaveCaretPosition(inputRef.current);

    input.onChange(newBases || "");
    setTimeout(() => {
      restore();
    }, 0);
  };

  useEffect(() => {
    CaretPositioning.restoreSelection(inputRef.current, caretPosition);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bases]);

  const basesToUse = bases || "";
  const { allBasesWithMetaData } = getStructuredBases({
    annotationRange: { start: start - 1, end: end - 1 },
    forward,
    bases: basesToUse,
    start: start - 1,
    end: end - 1,
    fullSequence: sequenceData.sequence,
    primerBindsOn,
    sequenceLength
  });
  let html = flatMap(
    allBasesWithMetaData,
    ({ b, isMatch, isAmbiguousMatch }) => {
      if (b === "&") return [];
      return `<span class="${isMatch
          ? ""
          : isAmbiguousMatch
            ? "tg-ambiguous-match-seq"
            : "tg-no-match-seq"
        }">${b}</span>`;
    }
  );
  html = html.join("");

  return (
    <div
      style={{
        display: "flex",
        ...(disabled ? { pointerEvents: "none" } : {})
      }}
    >
      <span
        style={{
          verticalAlign: "top",
          marginTop: 9,
          marginRight: 3,
          fontSize: 12,
          color: "grey"
        }}
      >
        5'
      </span>
      <span
        ref={inputRef}
        spellCheck="false"
        contentEditable={!disabled}
        className={classNames("bp3-input tg-custom-sequence-editable", {
          hasTempError
        })}
        onInput={emitChange}
        dangerouslySetInnerHTML={{ __html: html }} // innerHTML of the editable div
      />
      <span
        style={{
          alignSelf: "end",
          marginBottom: 9,
          marginLeft: 3,
          fontSize: 12,
          color: "grey"
        }}
      >
        3'
      </span>
    </div>
  );
});

const RenderBases = props => {
  const {
    sequenceData,
    readOnly,
    start,
    end,
    linkedOligo,
    getLinkedOligoLink,
    useLinkedOligo,
    sequenceLength,
    primerBindsOn,
    bases,
    forward,
    defaultLinkedOligoMessage,
    change
  } = props;
  let defaultValue;
  const seqLen = sequenceData.sequence.length;
  const validate = useMemo(() => {
    return (val /* vals, props */) => {
      if (!val) return;
      if (val.length > seqLen) {
        return "Primer cannot be longer than sequence";
      }
      return;
    };
  }, [seqLen]);
  const normalizedSelection = convertRangeTo0Based({
    start,
    end
  });
  if (!bases) {
    let bps = getSequenceWithinRange(
      normalizedSelection,
      sequenceData.sequence
    );
    if (!forward) {
      bps = getReverseComplementSequenceString(bps);
    }
    defaultValue = bps;
  }
  return (
    <div
      style={{
        borderTop: "1px solid #A7B6C2",
        borderBottom: "1px solid #A7B6C2"
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between"
        }}
      >
        <CheckboxField
          name="useLinkedOligo"
          label="Linked Oligo?"
          tooltipInfo={`Check this box to link this primer to an oligo in your Oligo Library. If the primer bases match exactly the bases of an existing oligo, it will be linked to that existing oligo. If the bases don't match, a new oligo will be created in the library.`}
          noMarginBottom
          defaultValue={useLinkedOligo ?? true}
          disabled={readOnly}
        ></CheckboxField>
        {useLinkedOligo && (
          <div
            className="tg-linked-oligo-holder"
            style={{ marginTop: -5, fontStyle: "italic", fontSize: 11 }}
          >
            {(getLinkedOligoLink && getLinkedOligoLink(props)) ||
              linkedOligo ||
              (defaultLinkedOligoMessage !== undefined
                ? defaultLinkedOligoMessage
                : "Will Be Created On Save")}
          </div>
        )}
      </div>
      {useLinkedOligo && (
        <div>
          <CustomContentEditable
            // inlineLabel
            showErrorIfUntouched
            tooltipError
            primerBindsOn={primerBindsOn}
            validate={validate}
            sequenceLength={sequenceLength}
            disabled={readOnly}
            {...props}
            defaultValue={bases ?? defaultValue}
            name="bases"
            label={
              <div className="tg-bases-label">
                <div style={{ display: "flex" }}>
                  Bases{" "}
                  <div style={{ fontSize: 10 }}>
                    {" "}
                    &nbsp; (Length: {bases ? bases.length : 0})
                  </div>
                </div>
                <div style={{ width: "fit-content" }}>
                  <DropdownButton
                    disabled={readOnly}
                    intent="primary"
                    small
                    menu={
                      <Menu>
                        <MenuItem
                          onClick={() => {
                            change("forward", true);
                            change(
                              "bases",
                              getSequenceWithinRange(
                                normalizedSelection,
                                sequenceData.sequence
                              )
                            );
                          }}
                          key="forward"
                          text="Forward"
                        ></MenuItem>
                        <MenuItem
                          onClick={() => {
                            change("forward", false);
                            change(
                              "bases",
                              getReverseComplementSequenceString(
                                getSequenceWithinRange(
                                  normalizedSelection,
                                  sequenceData.sequence
                                )
                              )
                            );
                          }}
                          key="reverse"
                          text="Reverse"
                        ></MenuItem>
                      </Menu>
                    }
                  >
                    Set From Selection
                  </DropdownButton>
                </div>
              </div>
            }
          />
          <AdvancedOptions style={{ marginBottom: 10 }}>
            <RadioGroupField
              name="primerBindsOn"
              inline
              inlineLabel
              disabled={readOnly}
              label="Oligo Binds On"
              tooltipError
              options={[
                { label: "5' End", value: "5prime" },
                { label: "3' End", value: "3prime" }
              ]}
            ></RadioGroupField>
          </AdvancedOptions>

          <MeltingTemp
            InnerWrapper={TextInnerWrapper}
            sequence={bases}
          ></MeltingTemp>
          <TextInnerWrapper>
            GC content: {bases && calculatePercentGC(bases).toFixed(1)}%
          </TextInnerWrapper>
          <TextInnerWrapper>
            3' Stability: {bases && calculateEndStability(bases)} kcal/mol
          </TextInnerWrapper>
        </div>
      )}
    </div>
  );
};

const TextInnerWrapper = p => (
  <div
    className="bp3-text-muted bp3-text-small"
    style={{
      marginBottom: 15,
      marginTop: -5,
      fontStyle: "italic"
    }}
  >
    {p.children}
  </div>
);

/**
 * New simplified primer dialog that takes a sequence input and auto-finds binding sites
 */
export default AddOrEditAnnotationDialog({
  formName: "AddOrEditPrimerDialog",
  getProps: props => ({
    upsertAnnotation: props.upsertPrimer,
    annotationTypePlural: "primers",
    RenderBases,
    // Override to use sequence-based binding detection
    RenderPrimerSequenceInput: PrimerSequenceInput
  })
});
